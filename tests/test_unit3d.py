"""Tests for the UNIT3D provider against a local mock of its subtitle API.

Every test asserts on the requests the mock received, the candidates
returned, the exceptions raised and the log lines written. The clock and the
HTTP opener are seams: the test drives the clock so budget and cooldown
arithmetic is exact, and the opener records the timeout of every request.
"""

import base64
import email.utils
import hashlib
import http.server
import importlib.util
import io
import json
import socket
import ssl
import sys
import threading
import time
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "unit3d"
FIXTURE_DIR = ROOT / "tests" / "fixtures"
DOCUMENTED_FIELDS = (
    "provider",
    "id",
    "language",
    "release_info",
    "filename",
    "matches",
    "score",
    "score_without_hash",
    "score_out_of",
    "hash_verifiable",
    "hearing_impaired_verifiable",
    "hearing_impaired",
    "display",
    "provider_payload",
)
ENGLISH = {"alpha3": "eng", "hi": False, "forced": False}
MOVIE_VIDEO = {
    "kind": "movie",
    "title": "Dune",
    "year": 2021,
    "tmdb_id": 438631,
    "imdb_id": "tt1160419",
    "size": 4321,
    "original_name": "/media/Dune.2021.1080p.WEBRip.mkv",
}
EPISODE_VIDEO = {
    "kind": "episode",
    "series": "Breaking Bad",
    "title": "Pilot",
    "year": 2008,
    "season": 1,
    "episode": 2,
    "series_imdb_id": "tt0903747",
    "series_tvdb_id": 81189,
    "size": 900,
    "original_name": "breaking.bad.s01e02.720p.mkv",
}
SAMPLE_MOVIE_VIDEO = {
    "kind": "movie",
    "title": "Sample Feature",
    "year": 2020,
    "tmdb_id": 123456,
    "imdb_id": "tt1234567",
    "size": 1500000000,
    "original_name": "/media/Sample.Feature.2020.1080p.WEB-DL.x264-EXAMPLE.mkv",
}


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("unit3d_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROVIDER = _load_provider_module()
ALPHA3_ORDER = list(PROVIDER._LANGUAGE_CODES)


def _config(pairs, match_files=True, timeout_seconds=30):
    return {
        "endpoints": " ".join(url for url, _key in pairs),
        "api_keys": " ".join(key for _url, key in pairs),
        "match_files": match_files,
        "timeout_seconds": timeout_seconds,
    }


def _tag(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _json(payload, status=200, headers=None):
    body = json.dumps(payload).encode("utf-8")
    return status, {"Content-Type": "application/json", **(headers or {})}, body


def _page(records, page=1, last_page=1, matched_by="imdb"):
    return _json(
        {
            "data": records,
            "links": {},
            "meta": {
                "current_page": page,
                "last_page": last_page,
                "per_page": 50,
                "total": len(records),
                "matched_by": matched_by,
            },
        }
    )


def _record(record_id, **overrides):
    record = {
        "id": record_id,
        "language": "en",
        "language_name": "English",
        "extension": "srt",
        "filename": "[English Subtitle]Dune.2021.1080p.WEBRip.x264-GRP.srt",
        "size": 60,
        "downloads": 3,
        "uploader": "uploader1",
        "forced": None,
        "hearing_impaired": None,
        "torrent_id": 77,
        "release": "Dune.2021.1080p.WEBRip.x264-GRP",
        "type": "movie",
        "season": None,
        "episode": None,
        "pack": None,
        "tmdb_id": 438631,
        "tvdb_id": None,
        "imdb_id": "tt1160419",
        "release_match": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "download_url": f"/api/subtitles/{record_id}/download",
    }
    record.update(overrides)
    return record


def _fixture_bytes(name):
    return (FIXTURE_DIR / name).read_bytes()


def _fixture_json(name):
    return json.loads(_fixture_bytes(name).decode("utf-8"))


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802, http.server API
        parts = urllib.parse.urlsplit(self.path)
        query = {
            name: values[-1]
            for name, values in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()
        }
        record = {"path": parts.path, "query": query, "auth": self.headers.get("Authorization")}
        with self.server.lock:
            self.server.requests.append(record)
        status, headers, body = self.server.respond(record)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        del args


class _Upstream(http.server.ThreadingHTTPServer):
    def __init__(self, respond):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.respond = respond
        self.requests = []
        self.lock = threading.Lock()


class _SlowDripHandler(http.server.BaseHTTPRequestHandler):
    """Holds the first byte of a 40 byte body for two seconds, then sends one byte every 0.2 seconds.

    It pauses for three seconds after the fifth byte.
    """

    def do_GET(self):  # noqa: N802, http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "40")
        self.end_headers()
        # Event.wait instead of time.sleep: other tests replace time.sleep for the whole process.
        pause = threading.Event()
        try:
            # The first byte waits past the budget of the slow-drip tests, so their first read has to wait.
            pause.wait(2)
            for index in range(40):
                self.wfile.write(b" ")
                pause.wait(3 if index == 4 else 0.2)
        except OSError:
            return  # the client closed the connection at its deadline

    def log_message(self, *args):
        del args


class _HeldBodyHandler(http.server.BaseHTTPRequestHandler):
    """Sends the headers of a 40 byte body at once, then holds the body until the test releases it."""

    def do_GET(self):  # noqa: N802, http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "40")
        self.end_headers()
        # A bounded wait, so a body that is never released cannot hold the server thread for long.
        self.server.release.wait(2)
        try:
            self.wfile.write(b" " * 40)
        except OSError:
            return  # the client closed the connection at its deadline

    def log_message(self, *args):
        del args


class _ClockedBody:
    """A response stand-in with a Content-Length. Each read returns the next chunk and moves the clock on."""

    def __init__(self, chunks, clock, step):
        self.length = sum(len(chunk) for chunk in chunks)
        self.reads = 0
        self._chunks = list(chunks)
        self._clock = clock
        self._step = step

    def read1(self, size):
        del size
        self.reads += 1
        self._clock.now += self._step
        chunk = self._chunks.pop(0) if self._chunks else b""
        self.length -= len(chunk)
        return chunk


class _TLSDroppingBody:
    """A response stand-in whose body read fails with a TLS error after the headers arrived."""

    length = 40
    headers = {"Content-Type": "application/x-subrip"}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        del exc_info
        return False

    def getcode(self):
        return 200

    def read1(self, size):
        del size
        raise ssl.SSLError("the TLS connection dropped mid-body")


class UNIT3DTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = PROVIDER
        self.clock = _Clock()
        self.timeouts = []
        clock_patch = patch.object(self.mod, "_monotonic", self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        original_open = self.mod._OPENER.open

        def recording_open(fullurl, data=None, timeout=None):
            self.timeouts.append(timeout)
            return original_open(fullurl, data=data, timeout=timeout)

        open_patch = patch.object(self.mod._OPENER, "open", recording_open)
        open_patch.start()
        self.addCleanup(open_patch.stop)

    def serve(self, respond):
        server = _Upstream(respond)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def search(self, pairs, video=None, languages=None, provider=None, match_files=True, timeout_seconds=30):
        provider = provider or self.mod.UNIT3DProvider()
        return provider.search(
            video=dict(MOVIE_VIDEO if video is None else video),
            languages=[ENGLISH] if languages is None else list(languages),
            config=_config(pairs, match_files=match_files, timeout_seconds=timeout_seconds),
        )


class ConfigurationTests(UNIT3DTestCase):
    def test_zero_endpoints_is_a_configuration_error(self):
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([])

    def test_endpoint_and_key_counts_must_match(self):
        config = {
            "endpoints": "https://a.example https://b.example",
            "api_keys": "key-a",
            "match_files": True,
            "timeout_seconds": 30,
        }
        with self.assertRaisesRegex(self.mod.ConfigurationError, r"2 endpoint\(s\) but 1 API key\(s\)"):
            self.mod.UNIT3DProvider().search(video=dict(MOVIE_VIDEO), languages=[ENGLISH], config=config)

    def test_invalid_endpoints_are_rejected(self):
        for url in (
            "ftp://tracker.example",
            "https://",
            "https://user:secret@tracker.example",
            "https://tracker.example/?key=1",
            "https://tracker.example/#fragment",
            "https://tracker.example:99999",
            "http://[::1",
        ):
            with self.subTest(url=url), self.assertRaises(self.mod.ConfigurationError):
                self.search([(url, "key-a")])

    def test_duplicate_normalized_endpoints_are_rejected(self):
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([("https://Tracker.example/", "key-a"), ("https://tracker.example", "key-b")])

    def test_timeout_must_be_a_whole_number_between_ten_and_sixty(self):
        for value in (9, 61, "soon", "9" * 5000):
            with self.subTest(value=value), self.assertRaises(self.mod.ConfigurationError):
                self.search([("https://tracker.example", "key-a")], timeout_seconds=value)

    def test_non_ascii_api_key_is_rejected_without_echoing_it(self):
        _server, base = self.serve(lambda record: _page([]))
        with self.assertRaises(self.mod.ConfigurationError) as caught:
            self.search([(base, "key-SECRET-\u00e9")])
        message = str(caught.exception)
        self.assertIn("UNIT3D endpoint 1", message)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("\u00e9", message)

    def test_non_ascii_endpoint_path_is_rejected_without_echoing_it(self):
        _server, base = self.serve(lambda record: _page([]))
        with self.assertRaises(self.mod.ConfigurationError) as caught:
            self.search([(f"{base}/track\u00e9r", "key-a")])
        message = str(caught.exception)
        self.assertIn("UNIT3D endpoint 1", message)
        self.assertNotIn("track", message)
        self.assertNotIn("\u00e9", message)

    def test_more_than_sixteen_endpoints_keep_the_first_sixteen_and_log_the_drop(self):
        def respond(record):
            number = int(record["path"].split("/t", 1)[1].split("/", 1)[0])
            return _page([_record(number + 1)])

        server, base = self.serve(respond)
        pairs = [(f"{base}/t{number}", f"key-{number}") for number in range(17)]
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search(pairs)
        self.assertEqual(len(results), 16)
        self.assertEqual(
            {record["path"] for record in server.requests},
            {f"/t{number}/api/subtitles" for number in range(16)},
        )
        self.assertIn("16", "\n".join(logs.output))


class SearchRequestTests(UNIT3DTestCase):
    def test_movie_search_sends_identity_file_and_paging_parameters(self):
        server, base = self.serve(lambda record: _page([]))
        self.search([(base, "key-a")])
        (request,) = server.requests
        self.assertEqual(request["path"], "/api/subtitles")
        self.assertEqual(request["auth"], "Bearer key-a")
        self.assertEqual(
            request["query"],
            {
                "type": "movie",
                "tmdb_id": "438631",
                "imdb_id": "tt1160419",
                "language": "en",
                "perPage": "50",
                "page": "1",
                "file_size": "4321",
                "file_name": "Dune.2021.1080p.WEBRip.mkv",
            },
        )

    def test_episode_search_uses_series_ids_season_and_episode(self):
        server, base = self.serve(lambda record: _page([]))
        self.search([(base, "key-a")], video=EPISODE_VIDEO)
        (request,) = server.requests
        self.assertEqual(
            request["query"],
            {
                "type": "episode",
                "season": "1",
                "episode": "2",
                "tvdb_id": "81189",
                "imdb_id": "tt0903747",
                "language": "en",
                "perPage": "50",
                "page": "1",
                "file_size": "900",
                "file_name": "breaking.bad.s01e02.720p.mkv",
            },
        )

    def test_title_search_needs_a_year_and_sends_no_ids(self):
        server, base = self.serve(lambda record: _page([]))
        video = {"kind": "movie", "title": "Dune", "year": 2021, "size": 4321}
        self.search([(base, "key-a")], video=video, match_files=False)
        (request,) = server.requests
        self.assertEqual(
            request["query"],
            {"type": "movie", "title": "Dune", "year": "2021", "language": "en", "perPage": "50", "page": "1"},
        )
        self.assertEqual(
            self.search([(base, "key-a")], video={"kind": "movie", "title": "Dune"}, match_files=False),
            [],
        )
        self.assertEqual(len(server.requests), 1)

    def test_match_files_off_sends_no_file_parameters(self):
        server, base = self.serve(lambda record: _page([]))
        self.search([(base, "key-a")], match_files=False)
        (request,) = server.requests
        self.assertNotIn("file_size", request["query"])
        self.assertNotIn("file_name", request["query"])

    def test_file_name_that_cannot_be_encoded_is_left_out_of_the_request(self):
        server, base = self.serve(lambda record: _page([_record(1)]))
        provider = self.mod.UNIT3DProvider()
        video = dict(MOVIE_VIDEO, original_name="Show\udcffname.mkv")
        (candidate,) = self.search([(base, "key-a")], video=video, provider=provider)
        self.assertEqual(candidate["id"], f"{_tag(base)}:1")
        (first,) = server.requests
        self.assertNotIn("file_name", first["query"])
        self.assertEqual(len(self.search([(base, "key-a")], provider=provider)), 1)
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(server.requests[1]["query"]["file_name"], "Dune.2021.1080p.WEBRip.mkv")

    def test_title_that_cannot_be_encoded_without_other_ids_sends_no_request(self):
        _server, base = self.serve(lambda record: _page([]))
        video = dict(MOVIE_VIDEO, title="Show\udcff")
        del video["imdb_id"], video["tmdb_id"]
        results = self.search([(base, "key-a")], video=video)
        self.assertEqual(results, [])
        self.assertEqual(self.timeouts, [])

    def test_forced_only_requests_and_unsupported_media_send_nothing(self):
        server, base = self.serve(lambda record: _page([]))
        forced = {"alpha3": "eng", "hi": False, "forced": True}
        self.assertEqual(self.search([(base, "key-a")], languages=[forced]), [])
        self.assertEqual(self.search([(base, "key-a")], video={"kind": "series", "title": "X"}), [])
        self.assertEqual(server.requests, [])

    def test_regional_portuguese_request_maps_to_the_base_language(self):
        server, base = self.serve(lambda record: _page([_record(9, language="pt")]))
        regional = {"alpha3": "por", "country_alpha2": "BR", "hi": False, "forced": False}
        (candidate,) = self.search([(base, "key-a")], languages=[regional])
        self.assertEqual(server.requests[0]["query"]["language"], "pt")
        self.assertEqual(candidate["language"]["alpha3"], "por")
        self.assertEqual(candidate["language"]["alpha2"], "pt")
        self.assertNotIn("country_alpha2", candidate["language"])

    def test_language_requests_split_into_chunks_of_fifty_in_input_order(self):
        server, base = self.serve(lambda record: _page([]))
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        self.search([(base, "key-a")], languages=languages)
        self.assertEqual(
            [request["query"]["language"] for request in server.requests],
            [",".join(alpha2_codes[:50]), ",".join(alpha2_codes[50:])],
        )
        self.assertEqual(self.timeouts, [30, 30])

    def test_failed_language_chunk_keeps_the_other_chunks_candidates(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]

        def respond(record):
            if record["query"]["language"].startswith(alpha2_codes[0] + ","):
                return _json({"error": "boom"}, status=500)
            return _page([_record(9, language=alpha2_codes[50])])

        _server, base = self.serve(respond)
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search([(base, "key-a")], languages=languages)
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:9"])
        self.assertIn("partial", "\n".join(logs.output))

    def test_refused_language_chunk_ends_the_endpoint_search_before_the_next_chunk(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]

        def respond(record):
            if record["query"]["language"].startswith(alpha2_codes[0] + ","):
                return _json({"message": "Unauthenticated."}, status=401)
            return _page([_record(9, language=alpha2_codes[50])])

        server, base = self.serve(respond)
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        with self.assertLogs("unit3d", level="WARNING"), self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], languages=languages)
        self.assertEqual(len(server.requests), 1)

    def test_two_failed_language_chunks_log_one_line_for_the_endpoint_search(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        _server, base = self.serve(lambda record: _json({"error": "boom"}, status=500))
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        with self.assertLogs("unit3d", level="WARNING") as logs, self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")], languages=languages)
        self.assertEqual([record.levelname for record in logs.records], ["WARNING"])

    def test_failed_chunk_then_refused_chunk_logs_and_raises_the_authentication_error(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]

        def respond(record):
            if record["query"]["language"].startswith(alpha2_codes[0] + ","):
                return _json({"error": "boom"}, status=500)
            return _json({"message": "Unauthenticated."}, status=401)

        _server, base = self.serve(respond)
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        with self.assertLogs("unit3d", level="WARNING") as logs, self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], languages=languages)
        self.assertEqual([record.levelname for record in logs.records], ["WARNING"])
        self.assertIn("AuthenticationError", "\n".join(logs.output))

    def test_failed_chunk_then_refused_chunk_makes_the_next_search_raise_from_the_cooldown(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]

        def respond(record):
            if record["query"]["language"].startswith(alpha2_codes[0] + ","):
                return _json({"error": "boom"}, status=500)
            return _json({"message": "Unauthenticated."}, status=401)

        server, base = self.serve(respond)
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        provider = self.mod.UNIT3DProvider()
        with self.assertLogs("unit3d", level="WARNING"), self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], languages=languages, provider=provider)
        requests_before = len(server.requests)
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], languages=languages, provider=provider)
        self.assertEqual(len(server.requests), requests_before)


class PaginationAndBudgetTests(UNIT3DTestCase):
    def test_second_page_is_requested_and_every_candidate_is_returned(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(number) for number in range(1, 51)], page=1, last_page=2)
            return _page([_record(51)], page=2, last_page=2)

        server, base = self.serve(respond)
        results = self.search([(base, "key-a")])
        self.assertEqual([request["query"]["page"] for request in server.requests], ["1", "2"])
        self.assertEqual(len(results), 51)

    def test_later_page_failure_keeps_earlier_candidates_and_marks_partial(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(number) for number in range(1, 51)], last_page=2)
            return _json({}, status=503)

        _server, base = self.serve(respond)
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search([(base, "key-a")])
        self.assertEqual(len(results), 50)
        self.assertIn("partial", "\n".join(logs.output))

    def test_later_page_timeout_keeps_earlier_candidates_and_does_not_cool_the_endpoint(self):
        server, base = self.serve(lambda record: _page([_record(1)], last_page=2))
        recording_open = self.mod._OPENER.open

        def open_timing_out_on_page_two(fullurl, data=None, timeout=None):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(fullurl.full_url).query)
            if query.get("page") == ["2"]:
                # The error a stalled socket raises, so the test does not wait out the 10 second minimum budget.
                raise TimeoutError("timed out")
            return recording_open(fullurl, data=data, timeout=timeout)

        provider = self.mod.UNIT3DProvider()
        with patch.object(self.mod._OPENER, "open", open_timing_out_on_page_two):
            with self.assertLogs("unit3d", level="WARNING") as logs:
                results = self.search([(base, "key-a")], provider=provider)
            self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])
            self.assertIn("returned partial results", "\n".join(logs.output))
            requests_before = len(server.requests)
            self.search([(base, "key-a")], provider=provider)
        self.assertGreater(len(server.requests), requests_before)

    def test_next_page_timeout_is_the_budget_left_after_the_previous_page(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                self.clock.now += 7
                return _page([_record(1)], last_page=2)
            return _page([_record(2)], page=2, last_page=2)

        _server, base = self.serve(respond)
        results = self.search([(base, "key-a")])
        self.assertEqual(self.timeouts, [30, 23])
        self.assertEqual(len(results), 2)

    def test_spent_budget_skips_the_next_page_with_a_logged_reason(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(number) for number in range(1, 51)], last_page=2)
            return _page([_record(99)], page=2, last_page=2)

        original_parse = self.mod._parse_page

        def parse_and_spend_budget(body):
            # The budget runs out once page one has arrived in full, so its body read is not cut short.
            parsed = original_parse(body)
            self.clock.now += 30
            return parsed

        server, base = self.serve(respond)
        with patch.object(self.mod, "_parse_page", parse_and_spend_budget), self.assertLogs(
            "unit3d", level="INFO"
        ) as logs:
            results = self.search([(base, "key-a")])
        self.assertEqual([request["query"]["page"] for request in server.requests], ["1"])
        self.assertEqual(len(results), 50)
        self.assertIn("budget", "\n".join(logs.output))

    def test_budget_spent_before_the_first_request_sends_nothing_and_cools_nothing(self):
        server, base = self.serve(lambda record: _page([_record(1)]))
        provider = self.mod.UNIT3DProvider()
        endpoint = self.mod._Endpoint(_tag(base), base, "127.0.0.1", "key-a")
        with self.assertLogs("unit3d", level="INFO") as logs:
            outcome = provider._search_endpoint(endpoint, MOVIE_VIDEO, {}, [[("eng", "en")]], set(), self.clock.now)
        self.assertEqual(server.requests, [])
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual(outcome.candidates, [])
        self.assertIn("budget", "\n".join(logs.output))
        self.assertEqual(len(self.search([(base, "key-a")], provider=provider)), 1)
        self.assertEqual(len(server.requests), 1)


class SlowBodyTests(UNIT3DTestCase):
    def serve_slow_drip(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowDripHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_slow_drip_search_body_is_cut_at_the_deadline(self):
        base = self.serve_slow_drip()
        endpoint = self.mod._Endpoint(_tag(base), base, "127.0.0.1", "key-a")
        with patch.object(self.mod, "_monotonic", time.monotonic):
            started = time.monotonic()
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.mod._fetch(endpoint, "/api/subtitles", None, 10, self.mod.MAX_SEARCH_BYTES, started + 1.0)
            self.assertLess(time.monotonic() - started, 1.5)

    def test_slow_drip_download_is_a_candidate_skip_within_the_budget(self):
        base = self.serve_slow_drip()
        payload = {"provider": "unit3d", "schema": 1, "endpoint": _tag(base), "subtitle_id": 42, "extension": "srt"}
        offset = [0.0]  # budget time the test spends once the headers have arrived
        recording_open = self.mod._OPENER.open

        def open_then_spend_budget(fullurl, data=None, timeout=None):
            response = recording_open(fullurl, data=data, timeout=timeout)
            # The budget left is 0.4 seconds. The server holds the first body byte for two seconds, so the first
            # read waits, and it must end at the 0.4 seconds left rather than at the 1 second socket timeout.
            offset[0] = 0.6
            return response

        def clock():
            return time.monotonic() + offset[0]

        with patch.object(self.mod, "_monotonic", clock), patch.object(
            self.mod._OPENER, "open", open_then_spend_budget
        ), patch.object(self.mod, "DOWNLOAD_TIMEOUT_SECONDS", 1):
            started = time.monotonic()
            with self.assertLogs("unit3d", level="WARNING"):
                content = self.mod.UNIT3DProvider().download(
                    provider_payload=payload,
                    language=ENGLISH,
                    config=_config([(base, "key-a")]),
                )
            elapsed = time.monotonic() - started
        self.assertTrue(content["empty"])
        self.assertLess(elapsed, 1.0)

    def serve_held_body(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HeldBodyHandler)
        server.release = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.release.set)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_body_released_after_the_deadline_is_cut_at_the_deadline(self):
        base = self.serve_held_body()
        endpoint = self.mod._Endpoint(_tag(base), base, "127.0.0.1", "key-a")
        with patch.object(self.mod, "_monotonic", time.monotonic):
            started = time.monotonic()
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.mod._fetch(endpoint, "/api/subtitles", None, 10, self.mod.MAX_SEARCH_BYTES, started + 0.5)
            self.assertLess(time.monotonic() - started, 1.5)

    def test_deadline_passed_between_chunks_ends_the_read_without_a_socket(self):
        body = _ClockedBody([b"x" * 8] * 4, self.clock, step=1.0)
        started = self.clock.now
        with patch.object(self.mod, "_response_socket", lambda response: None):
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.mod._read_body(body, self.mod.MAX_SEARCH_BYTES, started + 2.5)
        self.assertEqual(body.reads, 3)
        self.assertLess(self.clock.now - started, 3.5)

    def test_deadline_passed_before_the_first_read_reads_nothing_without_a_socket(self):
        body = _ClockedBody([b"x" * 8] * 4, self.clock, step=1.0)
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.mod._read_body(body, self.mod.MAX_SEARCH_BYTES, self.clock.now - 1.0)
        self.assertEqual(body.reads, 0)


class TruncatedBodyTests(UNIT3DTestCase):
    def serve_truncated(self):
        """Declares a 40 byte body, sends 20 bytes and closes the connection."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def declare_forty_send_twenty():
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(65536)
                    if not chunk:
                        return
                    request += chunk
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/x-subrip\r\nContent-Length: 40\r\n"
                    b"Connection: close\r\n\r\n" + b"1" * 20
                )

        threading.Thread(target=declare_forty_send_twenty, daemon=True).start()
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    def test_body_shorter_than_its_content_length_is_a_service_unavailable(self):
        base = self.serve_truncated()
        endpoint = self.mod._Endpoint(_tag(base), base, "127.0.0.1", "key-a")
        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "closed the connection before the body was complete"):
            self.mod._fetch(endpoint, "/api/subtitles", None, 10, self.mod.MAX_SEARCH_BYTES, self.clock.now + 30)

    def test_truncated_download_is_a_candidate_skip_with_one_log_line(self):
        base = self.serve_truncated()
        payload = {"provider": "unit3d", "schema": 1, "endpoint": _tag(base), "subtitle_id": 42, "extension": "srt"}
        with self.assertLogs("unit3d", level="WARNING") as logs:
            content = self.mod.UNIT3DProvider().download(
                provider_payload=payload,
                language=ENGLISH,
                config=_config([(base, "key-a")]),
            )
        self.assertEqual(content, self.mod._empty_download("srt"))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("before the body was complete", logs.output[0])


class FailureIsolationTests(UNIT3DTestCase):
    def test_failed_endpoint_does_not_stop_a_healthy_endpoint(self):
        _sick, sick_base = self.serve(lambda record: _json({"error": "down"}, status=500))
        _well, well_base = self.serve(lambda record: _page([_record(5)]))
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search([(sick_base, "key-sick"), (well_base, "key-well")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(well_base)}:5"])
        text = "\n".join(logs.output)
        self.assertIn(_tag(sick_base), text)
        self.assertIn("ServiceUnavailable", text)
        self.assertNotIn("key-sick", text)

    def test_rate_limited_endpoint_does_not_stop_a_healthy_endpoint(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        throttled, base_a = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "120"}))
        healthy, base_b = self.serve(lambda record: _page([_record(5)]))
        results = self.search([(base_a, "key-a"), (base_b, "key-b")], languages=languages)
        self.assertTrue(throttled.requests)
        self.assertEqual(len(healthy.requests), 2)
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base_b)}:5"])

    def test_every_endpoint_unauthorized_raises_authentication_error(self):
        _a, base_a = self.serve(lambda record: _json({"message": "Unauthenticated."}, status=401))
        _b, base_b = self.serve(lambda record: _json({"message": "Forbidden"}, status=403))
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base_a, "key-a"), (base_b, "key-b")])

    def test_every_endpoint_rate_limited_raises_api_throttled_with_the_longest_retry_after(self):
        _a, base_a = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "120"}))
        _b, base_b = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "90"}))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base_a, "key-a"), (base_b, "key-b")])
        self.assertEqual(caught.exception.retry_after, 120)

    def test_mixed_failures_raise_service_unavailable(self):
        _a, base_a = self.serve(lambda record: _json({}, status=401))
        _b, base_b = self.serve(lambda record: _json({}, status=500))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base_a, "key-a"), (base_b, "key-b")])

    def test_every_endpoint_without_the_api_raises_configuration_error(self):
        _a, base_a = self.serve(lambda record: _json({"message": "Not Found"}, status=404))
        _b, base_b = self.serve(lambda record: _json({"message": "Not Found"}, status=404))
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([(base_a, "key-a"), (base_b, "key-b")])

    def test_unprocessable_requests_log_an_error_and_raise_configuration_error(self):
        _a, base_a = self.serve(lambda record: _json({"errors": {}}, status=422))
        _b, base_b = self.serve(lambda record: _json({"errors": {}}, status=422))
        with self.assertLogs("unit3d", level="ERROR"), self.assertRaises(self.mod.ConfigurationError):
            self.search([(base_a, "key-a"), (base_b, "key-b")])

    def test_redirects_are_refused_and_the_key_is_not_resent(self):
        target, target_base = self.serve(lambda record: _page([_record(1)]))
        _redirector, base = self.serve(
            lambda record: (302, {"Location": f"{target_base}/api/subtitles"}, b"")
        )
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])
        self.assertEqual(target.requests, [])

    def test_tls_handshake_failure_is_a_service_unavailable(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def answer_in_plain_text():
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            with connection:
                connection.recv(65536)
                connection.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        threading.Thread(target=answer_in_plain_text, daemon=True).start()
        base = f"https://127.0.0.1:{listener.getsockname()[1]}"
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])

    def test_tls_error_while_reading_a_body_is_a_service_unavailable(self):
        base = "https://tracker.example"
        endpoint = self.mod._Endpoint(_tag(base), base, "tracker.example", "key-a")
        with patch.object(self.mod._OPENER, "open", lambda *args, **kwargs: _TLSDroppingBody()):
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.mod._fetch(endpoint, "/api/subtitles", None, 10, self.mod.MAX_SEARCH_BYTES, self.clock.now + 30)

    def test_unreachable_endpoint_is_service_unavailable(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(f"http://127.0.0.1:{port}", "key-a")])

    def test_malformed_response_is_an_endpoint_failure_not_an_empty_result(self):
        _bad, bad_base = self.serve(lambda record: (200, {"Content-Type": "text/html"}, b"<html>oops</html>"))
        _well, well_base = self.serve(lambda record: _page([_record(3)]))
        results = self.search([(bad_base, "key-bad"), (well_base, "key-well")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(well_base)}:3"])
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(bad_base, "key-bad")])

    def test_valid_empty_results_are_a_success(self):
        _a, base_a = self.serve(lambda record: _page([]))
        _b, base_b = self.serve(lambda record: _page([]))
        self.assertEqual(self.search([(base_a, "key-a"), (base_b, "key-b")]), [])

    def test_failure_log_never_carries_the_api_key_or_the_response_body(self):
        _sick, sick_base = self.serve(lambda record: _json({"detail": "SECRET-BODY"}, status=500))
        _well, well_base = self.serve(lambda record: _page([_record(5)]))
        with self.assertLogs("unit3d", level="WARNING") as logs:
            self.search([(sick_base, "key-sick-value"), (well_base, "key-well-value")])
        text = "\n".join(logs.output)
        self.assertNotIn("SECRET-BODY", text)
        self.assertNotIn("key-sick-value", text)

    def test_unexpected_error_in_one_endpoint_keeps_the_other_endpoints_candidates(self):
        _bad, bad_base = self.serve(lambda record: _page([_record(1)]))
        _well, well_base = self.serve(lambda record: _page([_record(5)]))
        bad_tag = _tag(bad_base)
        original_candidate = self.mod._candidate

        def failing_candidate(video, endpoint, record, matched_by, requested):
            if endpoint.tag == bad_tag:
                raise RuntimeError("SECRET-MESSAGE")
            return original_candidate(video, endpoint, record, matched_by, requested)

        with patch.object(self.mod, "_candidate", failing_candidate):
            with self.assertLogs("unit3d", level="WARNING") as logs:
                results = self.search([(bad_base, "key-bad"), (well_base, "key-well")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(well_base)}:5"])
        text = "\n".join(logs.output)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SECRET-MESSAGE", text)

    def test_unexpected_error_outside_the_page_loop_fails_the_endpoint_without_a_cooldown(self):
        base = "https://tracker.example"
        (endpoint,) = self.mod._configured_endpoints(_config([(base, "key-a")]))
        video = dict(MOVIE_VIDEO)
        query = self.mod._search_query(video, True)
        pairs = self.mod._requested_codes([ENGLISH])
        requested = {alpha3 for alpha3, _alpha2 in pairs}
        deadline = self.clock.now + 30
        provider = self.mod.UNIT3DProvider()
        with patch.object(self.mod.UNIT3DProvider, "_search_endpoint", side_effect=RuntimeError("SECRET-MESSAGE")):
            with self.assertLogs("unit3d", level="WARNING") as logs:
                outcome = provider._search_contained(endpoint, video, query, [pairs], requested, deadline)
        self.assertEqual([record.levelname for record in logs.records], ["WARNING"])
        text = "\n".join(logs.output)
        self.assertIn(_tag(base), text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SECRET-MESSAGE", text)
        self.assertEqual(outcome.candidates, [])
        self.assertIsInstance(outcome.error, self.mod.ServiceUnavailable)
        self.assertEqual(str(outcome.error), "the endpoint failed unexpectedly")
        self.assertEqual(provider._cooldowns, {})

    def test_unexpected_error_on_a_later_page_keeps_earlier_candidates_and_is_not_cooled(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(1)], last_page=2)
            return _page([_record(2)], page=2, last_page=2)

        server, base = self.serve(respond)
        original_candidate = self.mod._candidate
        mappings = []

        def fail_on_the_second_mapping(video, endpoint, record, matched_by, requested):
            mappings.append(record["id"])
            if len(mappings) == 2:
                raise RuntimeError("unexpected")
            return original_candidate(video, endpoint, record, matched_by, requested)

        provider = self.mod.UNIT3DProvider()
        with patch.object(self.mod, "_candidate", fail_on_the_second_mapping):
            with self.assertLogs("unit3d", level="WARNING") as logs:
                results = self.search([(base, "key-a")], provider=provider)
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])
        self.assertIn("returned partial results", "\n".join(logs.output))
        requests_before = len(server.requests)
        self.search([(base, "key-a")], provider=provider)
        self.assertGreater(len(server.requests), requests_before)

    def test_mapping_failure_on_a_later_page_drops_that_page_and_keeps_earlier_pages(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(1)], last_page=2)
            return _page([_record(2), _record(3)], page=2, last_page=2)

        original_candidate = self.mod._candidate

        def fail_on_the_second_record(video, endpoint, record, matched_by, requested):
            if record["id"] == 3:
                raise RuntimeError("unexpected")
            return original_candidate(video, endpoint, record, matched_by, requested)

        _server, base = self.serve(respond)
        with patch.object(self.mod, "_candidate", fail_on_the_second_record):
            with self.assertLogs("unit3d", level="WARNING"):
                results = self.search([(base, "key-a")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])

    def test_deeply_nested_search_page_is_a_service_unavailable(self):
        nested = b"[" * 100000 + b"]" * 100000
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/json"}, nested))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])

    def test_huge_current_page_digit_string_is_a_service_unavailable(self):
        _server, base = self.serve(lambda record: _page([_record(1)], page="9" * 5000))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])

    def test_deeply_nested_later_page_keeps_the_first_page_candidates(self):
        nested = b"[" * 100000 + b"]" * 100000

        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(1)], last_page=2)
            return (200, {"Content-Type": "application/json"}, nested)

        server, base = self.serve(respond)
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search([(base, "key-a")])
        self.assertEqual([request["query"]["page"] for request in server.requests], ["1", "2"])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])
        text = "\n".join(logs.output)
        self.assertIn("partial", text)
        self.assertNotIn("unexpectedly", text)

    def test_huge_later_page_digit_string_keeps_the_first_page_candidates(self):
        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(1)], last_page=2)
            return _page([_record(2)], page="9" * 5000, last_page=2)

        server, base = self.serve(respond)
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search([(base, "key-a")])
        self.assertEqual([request["query"]["page"] for request in server.requests], ["1", "2"])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])
        text = "\n".join(logs.output)
        self.assertIn("partial", text)
        self.assertNotIn("unexpectedly", text)


class CooldownTests(UNIT3DTestCase):
    def test_rate_limit_without_retry_after_skips_the_endpoint_for_sixty_seconds(self):
        throttled, base_a = self.serve(lambda record: _json({}, status=429))
        _well, base_b = self.serve(lambda record: _page([_record(4)]))
        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.UNIT3DProvider()
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 59
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 2
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 2)

    def test_retry_after_beyond_the_float_range_counts_as_no_header(self):
        throttled, base = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "9" * 400}))
        pairs = [(base, "key-a")]
        provider = self.mod.UNIT3DProvider()
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search(pairs, provider=provider)
        self.assertEqual(caught.exception.retry_after, 60)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 59
        with self.assertRaises(self.mod.APIThrottled):
            self.search(pairs, provider=provider)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 1
        with self.assertRaises(self.mod.APIThrottled):
            self.search(pairs, provider=provider)
        self.assertEqual(len(throttled.requests), 2)

    def test_retry_after_http_date_waits_until_that_date(self):
        date = email.utils.formatdate(time.time() + 3600, usegmt=True)
        _throttled, base = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": date}))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base, "key-a")])
        self.assertGreaterEqual(caught.exception.retry_after, 3500)
        self.assertLessEqual(caught.exception.retry_after, 3601)

    def test_retry_after_longer_than_one_hour_counts_as_one_hour(self):
        # A count of 301 digits still fits in a float, so it is capped rather than treated as no header.
        for value in ("7200", "1" * 301):
            with self.subTest(length=len(value)):
                throttled, base = self.serve(
                    lambda record, value=value: _json({}, status=429, headers={"Retry-After": value})
                )
                pairs = [(base, "key-a")]
                provider = self.mod.UNIT3DProvider()
                with self.assertRaises(self.mod.APIThrottled) as caught:
                    self.search(pairs, provider=provider)
                self.assertEqual(caught.exception.retry_after, 3600)
                self.clock.now += 3599
                with self.assertRaises(self.mod.APIThrottled):
                    self.search(pairs, provider=provider)
                self.assertEqual(len(throttled.requests), 1)
                self.clock.now += 1
                with self.assertRaises(self.mod.APIThrottled):
                    self.search(pairs, provider=provider)
                self.assertEqual(len(throttled.requests), 2)

    def test_retry_after_of_one_hour_or_less_is_kept(self):
        for value, expected in (("1800", 1800), ("3600", 3600)):
            with self.subTest(value=value):
                _throttled, base = self.serve(
                    lambda record, value=value: _json({}, status=429, headers={"Retry-After": value})
                )
                with self.assertRaises(self.mod.APIThrottled) as caught:
                    self.search([(base, "key-a")])
                self.assertEqual(caught.exception.retry_after, expected)

    def test_retry_after_http_date_a_year_away_counts_as_one_hour(self):
        date = email.utils.formatdate(time.time() + 365 * 24 * 3600, usegmt=True)
        _throttled, base = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": date}))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base, "key-a")])
        self.assertGreaterEqual(caught.exception.retry_after, 3500)
        self.assertLessEqual(caught.exception.retry_after, 3601)

    def test_retry_after_of_zero_or_a_past_date_waits_for_nothing(self):
        past = email.utils.formatdate(time.time() - 3600, usegmt=True)
        for value in ("0", past):
            with self.subTest(value=value):
                _throttled, base = self.serve(
                    lambda record, value=value: _json({}, status=429, headers={"Retry-After": value})
                )
                with self.assertRaises(self.mod.APIThrottled) as caught:
                    self.search([(base, "key-a")])
                self.assertEqual(caught.exception.retry_after, 0)

    def test_unusable_retry_after_waits_the_default_sixty_seconds(self):
        for value in ("soon", "-5"):
            with self.subTest(value=value):
                _throttled, base = self.serve(
                    lambda record, value=value: _json({}, status=429, headers={"Retry-After": value})
                )
                with self.assertRaises(self.mod.APIThrottled) as caught:
                    self.search([(base, "key-a")])
                self.assertEqual(caught.exception.retry_after, 60)

    def test_retry_after_is_honored_even_when_another_endpoint_succeeds(self):
        throttled, base_a = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "120"}))
        _well, base_b = self.serve(lambda record: _page([_record(4)]))
        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.UNIT3DProvider()
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.clock.now += 119
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 2
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 2)

    def test_rate_limit_ends_the_endpoint_search_and_keeps_the_candidates_already_received(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]

        def respond(record):
            if int(record["query"]["page"]) == 1:
                return _page([_record(1)], last_page=2)
            return _json({}, status=429, headers={"Retry-After": "120"})

        server, base = self.serve(respond)
        with self.assertLogs("unit3d", level="WARNING") as logs:
            results = self.search([(base, "key-a")], languages=languages)
        self.assertEqual(
            [(request["query"]["language"], request["query"]["page"]) for request in server.requests],
            [(",".join(alpha2_codes[:50]), "1"), (",".join(alpha2_codes[:50]), "2")],
        )
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])
        text = "\n".join(logs.output)
        self.assertEqual(text.count("APIThrottled"), 1)
        self.assertIn("partial", text)

    def test_failed_search_ending_on_a_rate_limit_raises_api_throttled_and_replays_it(self):
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]

        def respond(record):
            if record["query"]["language"].startswith(alpha2_codes[0] + ","):
                return _json({}, status=500)
            return _json({}, status=429, headers={"Retry-After": "120"})

        server, base = self.serve(respond)
        provider = self.mod.UNIT3DProvider()
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], languages=languages, provider=provider)
        requests_before = len(server.requests)
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], languages=languages, provider=provider)
        self.assertEqual(len(server.requests), requests_before)

    def test_timed_out_endpoint_is_skipped_for_sixty_seconds_then_queried_again(self):
        _healthy, base_b = self.serve(lambda record: _page([_record(4)]))
        base_a = "https://timeout.example"
        attempts = []
        recording_open = self.mod._OPENER.open

        def open_timing_out(fullurl, data=None, timeout=None):
            if fullurl.full_url.startswith(f"{base_a}/"):
                attempts.append(fullurl.full_url)
                # The error a stalled socket raises, so the test does not wait out the 10 second minimum budget.
                raise TimeoutError("timed out")
            return recording_open(fullurl, data=data, timeout=timeout)

        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.UNIT3DProvider()
        with patch.object(self.mod._OPENER, "open", open_timing_out):
            self.assertEqual(len(self.search(pairs, provider=provider)), 1)
            self.assertEqual(len(attempts), 1)
            self.clock.now += 59
            self.assertEqual(len(self.search(pairs, provider=provider)), 1)
            self.assertEqual(len(attempts), 1)
            self.clock.now += 1
            self.assertEqual(len(self.search(pairs, provider=provider)), 1)
            self.assertEqual(len(attempts), 2)
            # Once every endpoint is cooling, the search reports the failure that cooled them.
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.search([(base_a, "key-a")], provider=provider)
            self.assertEqual(len(attempts), 2)

    def test_cooldown_applies_only_to_the_api_key_it_was_earned_with(self):
        def respond(record):
            if record["auth"] == "Bearer key-b":
                return _page([_record(4)])
            return _json({"message": "Unauthenticated."}, status=401)

        server, base = self.serve(respond)
        provider = self.mod.UNIT3DProvider()
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(self.search([(base, "key-b")], provider=provider)), 1)
        self.assertEqual([request["auth"] for request in server.requests], ["Bearer key-a", "Bearer key-b"])

    def test_shorter_cooldown_keeps_the_active_longer_one_and_its_error(self):
        base = "https://cooldown.example"
        (endpoint,) = self.mod._configured_endpoints(_config([(base, "key-a")]))
        provider = self.mod.UNIT3DProvider()
        provider._cool_down(endpoint, self.mod.APIThrottled("the tracker is rate limiting the API"), 3600)
        provider._cool_down(endpoint, self.mod.ServiceUnavailable("the endpoint is unreachable"))
        expiry, error = provider._cooldowns[self.mod._cooldown_key(endpoint)]
        self.assertEqual(expiry, self.clock.now + 3600)
        self.assertIsInstance(error, self.mod.APIThrottled)
        self.assertEqual(str(error), "the tracker is rate limiting the API")

    def test_rate_limit_reports_the_cooldown_in_force_when_a_longer_one_is_kept(self):
        _throttled, base = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "60"}))
        (endpoint,) = self.mod._configured_endpoints(_config([(base, "key-a")]))
        provider = self.mod.UNIT3DProvider()
        provider._cool_down(endpoint, self.mod.APIThrottled("the tracker is rate limiting the API"), 3600)
        with self.assertRaises(self.mod._Refused) as caught:
            self.mod._fetch(endpoint, "/api/subtitles", {}, 30, self.mod.MAX_SEARCH_BYTES, self.clock.now + 30)
        error = provider._refuse(endpoint, caught.exception)
        self.assertEqual(error.retry_after, 3600)
        expiry, _error = provider._cooldowns[self.mod._cooldown_key(endpoint)]
        self.assertEqual(expiry, self.clock.now + 3600)


class CandidateTests(UNIT3DTestCase):
    def test_candidate_carries_the_documented_fields_and_keeps_unknown_flags_unknown(self):
        _server, base = self.serve(lambda record: _page([_record(7)]))
        (candidate,) = self.search([(base, "key-a")])
        for field in DOCUMENTED_FIELDS:
            self.assertIn(field, candidate)
        self.assertEqual(candidate["id"], f"{_tag(base)}:7")
        self.assertIsNone(candidate["hearing_impaired"])
        self.assertFalse(candidate["hearing_impaired_verifiable"])
        self.assertIsNone(candidate["language"]["forced"])

    def test_hash_is_claimed_only_when_the_tracker_flags_the_exact_file(self):
        records = [
            _record(1, release_match={"file_size": True, "file_name": True}),
            _record(2, release_match={"file_size": True, "file_name": False}),
            _record(3, release_match=None),
        ]
        _server, base = self.serve(lambda record: _page(records))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        exact = by_id[f"{_tag(base)}:1"]
        self.assertIn("hash", exact["matches"])
        self.assertTrue(exact["hash_verifiable"])
        self.assertEqual(exact["release_info"], "Dune.2021.1080p.WEBRip.x264-GRP")
        for record_id in (2, 3):
            candidate = by_id[f"{_tag(base)}:{record_id}"]
            self.assertNotIn("hash", candidate["matches"])
            self.assertFalse(candidate["hash_verifiable"])

    def test_page_link_comes_from_the_configured_base_and_the_torrent_id(self):
        records = [
            _record(1, torrent_id=77, page_link="https://evil.example/x"),
            _record(2, torrent_id=None),
        ]
        _server, base = self.serve(lambda record: _page(records))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        self.assertEqual(by_id[f"{_tag(base)}:1"]["page_link"], f"{base}/torrents/77")
        self.assertNotIn("page_link", by_id[f"{_tag(base)}:2"])

    def test_worker_ids_are_namespaced_by_endpoint(self):
        _a, base_a = self.serve(lambda record: _page([_record(42)]))
        _b, base_b = self.serve(lambda record: _page([_record(42)]))
        ids = sorted(candidate["id"] for candidate in self.search([(base_a, "key-a"), (base_b, "key-b")]))
        self.assertEqual(ids, sorted([f"{_tag(base_a)}:42", f"{_tag(base_b)}:42"]))

    def test_only_subtitle_and_archive_formats_are_offered(self):
        records = [
            _record(1, extension="srt"),
            _record(2, extension="pdf"),
            _record(3, extension="zip"),
            _record(4, extension=".ASS"),
            _record(5, extension="sub"),
            _record(6, extension="rar"),
        ]
        _server, base = self.serve(lambda record: _page(records))
        ids = {candidate["id"].split(":", 1)[1] for candidate in self.search([(base, "key-a")])}
        self.assertEqual(ids, {"1", "3", "4"})

    def test_pack_records_defer_the_episode_match_to_the_archive(self):
        records = [
            _record(
                1, type="episode", season=1, episode=2, pack=None,
                tvdb_id=81189, imdb_id="tt0903747", tmdb_id=1396,
            ),
            _record(
                2, type="episode", extension="zip", season=1, episode=None, pack="season",
                tvdb_id=81189, imdb_id="tt0903747",
            ),
            _record(
                3, type="episode", extension="zip", season=None, episode=None, pack="series",
                tvdb_id=81189, imdb_id="tt0903747",
            ),
            _record(
                4, type="episode", season=1, episode=None, pack="season",
                tvdb_id=81189, imdb_id="tt0903747",
            ),
        ]
        _server, base = self.serve(lambda record: _page(records))
        by_id = {
            candidate["id"].split(":", 1)[1]: candidate
            for candidate in self.search([(base, "key-a")], video=EPISODE_VIDEO)
        }
        self.assertEqual(set(by_id), {"1", "2", "3"})
        self.assertTrue({"season", "episode"} <= set(by_id["1"]["matches"]))
        self.assertIn("season", by_id["2"]["matches"])
        self.assertNotIn("episode", by_id["2"]["matches"])
        self.assertFalse({"season", "episode"} & set(by_id["3"]["matches"]))
        self.assertEqual(by_id["2"]["provider_payload"]["season"], 1)
        self.assertEqual(by_id["2"]["provider_payload"]["episode"], 2)

    def test_records_of_another_media_type_are_not_offered(self):
        records = [
            _record(1, type="episode", season=1, episode=2, tvdb_id=81189, imdb_id="tt0903747"),
            _record(2, type="movie", season=None, episode=None, tvdb_id=None, imdb_id="tt0903747"),
        ]
        _server, base = self.serve(lambda record: _page(records))
        ids = {candidate["id"].split(":", 1)[1] for candidate in self.search([(base, "key-a")], video=EPISODE_VIDEO)}
        self.assertEqual(ids, {"1"})

    def test_episode_found_by_series_imdb_id_reports_the_series_match(self):
        records = [
            _record(
                1, type="episode", season=1, episode=2, pack=None,
                tvdb_id=None, imdb_id="tt0903747",
            ),
        ]
        _server, base = self.serve(lambda record: _page(records))
        (candidate,) = self.search([(base, "key-a")], video=EPISODE_VIDEO)
        self.assertCountEqual(
            candidate["matches"],
            ["series", "series_imdb_id", "season", "episode"],
        )

    def test_episode_matched_only_on_season_and_episode_does_not_report_series(self):
        records = [
            _record(
                1, type="episode", season=1, episode=2, pack=None,
                tvdb_id=None, imdb_id="tt1160419",
            ),
        ]
        _server, base = self.serve(lambda record: _page(records))
        (candidate,) = self.search([(base, "key-a")], video=EPISODE_VIDEO)
        self.assertNotIn("series", candidate["matches"])
        self.assertCountEqual(candidate["matches"], ["season", "episode"])

    def test_movie_matched_on_tmdb_id_earns_the_same_tier_as_an_imdb_match(self):
        records = [
            _record(1, tmdb_id=438631, imdb_id="tt9999999"),
            _record(2, tmdb_id=1, imdb_id="tt1160419"),
            _record(3, tmdb_id=1, imdb_id="tt9999999"),
        ]
        _server, base = self.serve(lambda record: _page(records))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        by_tmdb = by_id[f"{_tag(base)}:1"]
        by_imdb = by_id[f"{_tag(base)}:2"]
        neither = by_id[f"{_tag(base)}:3"]
        self.assertEqual(by_tmdb["matches"], ["tmdb_id"])
        self.assertEqual(by_imdb["matches"], ["imdb_id"])
        self.assertEqual(neither["matches"], [])
        self.assertEqual(by_tmdb["score_without_hash"], 95)
        self.assertEqual(by_imdb["score_without_hash"], 95)
        self.assertEqual(neither["score_without_hash"], 80)

    def test_unlabeled_hearing_impaired_stays_unlabeled_for_a_hi_request(self):
        _server, base = self.serve(lambda record: _page([_record(8)]))
        hi_request = {"alpha3": "eng", "hi": True, "forced": False}
        (candidate,) = self.search([(base, "key-a")], languages=[hi_request])
        self.assertIsNone(candidate["hearing_impaired"])
        self.assertFalse(candidate["hearing_impaired_verifiable"])
        self.assertIsNone(candidate["language"]["hi"])

    def test_huge_imdb_digit_string_is_treated_as_absent(self):
        _server, base = self.serve(lambda record: _page([_record(1, imdb_id="tt" + "9" * 5000)]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertEqual(candidate["matches"], ["tmdb_id"])

    def test_search_fixture_maps_each_result_to_a_candidate(self):
        body = _fixture_bytes("unit3d_search_movie.json")
        _server, base = self.serve(lambda request: (200, {"Content-Type": "application/json"}, body))
        by_id = {
            candidate["id"]: candidate
            for candidate in self.search([(base, "key-a")], video=SAMPLE_MOVIE_VIDEO)
        }
        self.assertEqual(set(by_id), {f"{_tag(base)}:101", f"{_tag(base)}:102"})
        exact = by_id[f"{_tag(base)}:101"]
        self.assertIn("hash", exact["matches"])
        self.assertTrue(exact["hash_verifiable"])
        self.assertEqual(exact["page_link"], f"{base}/torrents/4321")
        partial = by_id[f"{_tag(base)}:102"]
        self.assertNotIn("hash", partial["matches"])
        self.assertFalse(partial["hash_verifiable"])

    def test_record_fixture_becomes_a_hash_verified_candidate(self):
        record = _fixture_json("unit3d_record_movie.json")
        _server, base = self.serve(lambda request: _page([record]))
        (candidate,) = self.search([(base, "key-a")], video=SAMPLE_MOVIE_VIDEO)
        self.assertEqual(candidate["id"], f"{_tag(base)}:101")
        self.assertEqual(candidate["language"], {"alpha3": "eng", "alpha2": "en", "hi": None, "forced": None})
        self.assertEqual(candidate["release_info"], "Sample.Feature.2020.1080p.WEB-DL.x264-EXAMPLE")
        self.assertEqual(candidate["filename"], "[English Subtitle]Sample.Feature.2020.1080p.WEB-DL.x264-EXAMPLE.srt")
        self.assertCountEqual(candidate["matches"], ["tmdb_id", "imdb_id", "hash"])
        self.assertEqual(candidate["score"], 100)
        self.assertEqual(candidate["score_without_hash"], 95)
        self.assertTrue(candidate["hash_verifiable"])
        self.assertEqual(candidate["page_link"], f"{base}/torrents/4321")
        self.assertEqual(
            candidate["display"],
            {"source": "unit3d", "tracker": "127.0.0.1", "uploader": "example-uploader", "downloads": 12},
        )
        self.assertEqual(candidate["provider_payload"]["subtitle_id"], 101)


class DownloadTests(UNIT3DTestCase):
    def download(self, base, subtitle_id=42, extension="srt", episode=None, provider=None, season=None):
        payload = {
            "provider": "unit3d",
            "schema": 1,
            "endpoint": _tag(base),
            "subtitle_id": subtitle_id,
            "extension": extension,
        }
        if episode is not None:
            payload["episode"] = episode
        if season is not None:
            payload["season"] = season
        return (provider or self.mod.UNIT3DProvider()).download(
            provider_payload=payload,
            language=ENGLISH,
            config=_config([(base, "key-a")]),
        )

    def test_download_is_fetched_from_the_endpoint_named_by_the_tag(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        ahead, base_a = self.serve(lambda record: _json({}, status=404))
        served, base_b = self.serve(lambda record: (200, {"Content-Type": "application/x-subrip"}, body))
        payload = {
            "provider": "unit3d",
            "schema": 1,
            "endpoint": _tag(base_b),
            "subtitle_id": 42,
            "extension": "srt",
        }
        content = self.mod.UNIT3DProvider().download(
            provider_payload=payload,
            language=ENGLISH,
            config=_config([(base_a, "key-a"), (base_b, "key-b")]),
        )
        self.assertEqual(ahead.requests, [])
        (request,) = served.requests
        self.assertEqual(request["path"], "/api/subtitles/42/download")
        self.assertEqual(request["auth"], "Bearer key-b")
        self.assertEqual(base64.b64decode(content["content_b64"]), body)
        self.assertEqual(content["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["format"], "srt")
        self.assertFalse(content["empty"])

    def test_text_download_leaves_the_encoding_to_the_host(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/x-subrip"}, body))
        self.assertEqual(
            self.download(base),
            {
                "content_b64": base64.b64encode(body).decode("ascii"),
                "content_sha256": hashlib.sha256(body).hexdigest(),
                "content_type": "application/x-subrip",
                "format": "srt",
                "encoding": None,
                "empty": False,
            },
        )

    def test_download_fixture_is_returned_as_a_text_subtitle(self):
        body = _fixture_bytes("unit3d_download_movie.srt")
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/x-subrip"}, body))
        content = self.download(base)
        self.assertEqual(base64.b64decode(content["content_b64"]), body)
        self.assertEqual(content["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["format"], "srt")
        self.assertFalse(content["empty"])

    def test_missing_subtitle_is_a_candidate_skip_not_an_error(self):
        _server, base = self.serve(lambda record: _json({"message": "Not Found"}, status=404))
        content = self.download(base)
        self.assertTrue(content["empty"])
        self.assertEqual(content["content_b64"], "")

    def test_rejected_download_key_raises_authentication_error(self):
        for status in (401, 403):
            with self.subTest(status=status):
                _server, base = self.serve(lambda record, status=status: _json({"message": "revoked"}, status=status))
                with self.assertRaises(self.mod.AuthenticationError):
                    self.download(base)

    def test_json_body_on_a_download_is_a_candidate_skip(self):
        _server, base = self.serve(lambda record: _json({"message": "refused for this account"}))
        self.assertTrue(self.download(base)["empty"])

    def test_pack_archive_is_returned_unchanged_for_host_extraction(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Show.S01E02.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        archive_bytes = buffer.getvalue()
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/zip"}, archive_bytes))
        content = self.download(base, extension="zip", episode=2)
        self.assertEqual(base64.b64decode(content["archive_b64"]), archive_bytes)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(archive_bytes).hexdigest())
        self.assertEqual(content["episode"], 2)
        self.assertNotIn("encoding", content)
        self.assertNotIn("content_b64", content)

    def test_season_pack_archive_names_its_season_and_episode_for_host_extraction(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Show.S01E02.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        archive_bytes = buffer.getvalue()
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/zip"}, archive_bytes))
        content = self.download(base, extension="zip", episode=2, season=1)
        self.assertEqual(content["season"], 1)
        self.assertEqual(content["episode"], 2)
        # No select_archive_member method exists, so the archive contract forbids asking for one.
        self.assertFalse(hasattr(self.mod.UNIT3DProvider, "select_archive_member"))
        self.assertNotIn("select_member", content)

    def test_season_pack_download_passes_the_sdk_smoke_contract(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from sdk import cli as sdk_cli

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Show.S01E01.srt", "1\n00:00:01,000 --> 00:00:02,000\nOne\n")
            archive.writestr("Show.S01E02.srt", "1\n00:00:01,000 --> 00:00:02,000\nTwo\n")
        archive_bytes = buffer.getvalue()

        def respond(record):
            if record["path"].endswith("/download"):
                return 200, {"Content-Type": "application/zip"}, archive_bytes
            return _page(
                [
                    _record(
                        42, type="episode", extension="zip", pack="season", season=1, episode=None,
                        tvdb_id=81189, imdb_id="tt0903747", filename="Show.S01.Pack.zip",
                    )
                ]
            )

        _server, base = self.serve(respond)
        provider_id = sdk_cli.smoke_test(
            ROOT,
            "unit3d",
            config=_config([(base, "key-a")]),
            videos=[dict(EPISODE_VIDEO)],
            languages=[dict(ENGLISH)],
        )
        self.assertEqual(provider_id, "unit3d")

    def test_empty_and_html_downloads_are_candidate_skips(self):
        _empty, base_empty = self.serve(lambda record: (200, {"Content-Type": "text/plain"}, b""))
        _html, base_html = self.serve(
            lambda record: (200, {"Content-Type": "text/html"}, b"<!DOCTYPE html><html></html>")
        )
        with self.assertLogs("unit3d", level="WARNING") as logs:
            self.assertTrue(self.download(base_empty)["empty"])
            self.assertTrue(self.download(base_html)["empty"])
        self.assertEqual(len(logs.output), 2)

    def test_other_refused_downloads_are_candidate_skips_with_one_log_line_each(self):
        for status in (422, 500):
            with self.subTest(status=status):
                _server, base = self.serve(lambda record, status=status: _json({}, status=status))
                with self.assertLogs("unit3d", level="WARNING") as logs:
                    self.assertTrue(self.download(base)["empty"])
                self.assertEqual(len(logs.output), 1)
                self.assertIn(f"HTTP {status}", logs.output[0])

    def test_redirected_download_is_a_candidate_skip_and_the_key_is_not_resent(self):
        target, target_base = self.serve(lambda record: _page([_record(1)]))
        _redirector, base = self.serve(
            lambda record: (302, {"Location": f"{target_base}/api/subtitles/42/download"}, b"")
        )
        self.assertTrue(self.download(base)["empty"])
        self.assertEqual(target.requests, [])

    def test_unreachable_download_is_a_candidate_skip(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertTrue(self.download(f"http://127.0.0.1:{port}")["empty"])

    def test_rate_limited_download_raises_api_throttled_and_cools_the_endpoint(self):
        throttled, base = self.serve(lambda record: _json({}, status=429, headers={"Retry-After": "120"}))
        provider = self.mod.UNIT3DProvider()
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.download(base, provider=provider)
        self.assertEqual(caught.exception.retry_after, 120)
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(throttled.requests), 1)

    def test_rar_body_on_a_download_is_a_candidate_skip(self):
        rar = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 64
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/octet-stream"}, rar))
        with self.assertLogs("unit3d", level="WARNING") as logs:
            content = self.download(base, extension="zip")
        self.assertTrue(content["empty"])
        self.assertEqual(len(logs.output), 1)

    def test_deeply_nested_json_download_is_a_candidate_skip(self):
        nested = b"[" * 100000 + b"]" * 100000
        _server, base = self.serve(lambda record: (200, {"Content-Type": "text/plain"}, nested))
        self.assertTrue(self.download(base)["empty"])

    def test_download_for_an_endpoint_that_is_not_configured_is_a_candidate_skip(self):
        server, base = self.serve(lambda record: (200, {}, b"x"))
        payload = {"provider": "unit3d", "endpoint": "0" * 16, "subtitle_id": 42, "extension": "srt"}
        with self.assertLogs("unit3d", level="WARNING") as logs:
            content = self.mod.UNIT3DProvider().download(
                provider_payload=payload,
                language=ENGLISH,
                config=_config([(base, "key-a")]),
            )
        self.assertTrue(content["empty"])
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(server.requests, [])


class HearingImpairedRoutingTests(UNIT3DTestCase):
    """The host maps a hearing-impaired request to its base language before the worker call."""

    def test_base_language_search_reports_both_flags_as_unknown(self):
        body = _fixture_bytes("unit3d_search_movie.json")
        records = json.loads(body.decode("utf-8"))["data"]
        self.assertTrue(all(r["hearing_impaired"] is None and r["forced"] is None for r in records))
        server, base = self.serve(lambda request: (200, {"Content-Type": "application/json"}, body))
        candidates = self.search([(base, "key-a")], video=SAMPLE_MOVIE_VIDEO)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(server.requests[0]["query"]["language"], "en")
        for candidate in candidates:
            self.assertIsNone(candidate["hearing_impaired"])
            self.assertFalse(candidate["hearing_impaired_verifiable"])
            self.assertEqual(candidate["language"], {"alpha3": "eng", "alpha2": "en", "hi": None, "forced": None})

    def test_tracker_hearing_impaired_true_is_still_reported_as_unknown(self):
        payload = _fixture_json("unit3d_search_movie.json")
        for record in payload["data"]:
            record["hearing_impaired"] = True
            record["forced"] = True
        body = json.dumps(payload).encode("utf-8")
        _server, base = self.serve(lambda request: (200, {"Content-Type": "application/json"}, body))
        candidates = self.search([(base, "key-a")], video=SAMPLE_MOVIE_VIDEO)
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
            self.assertIsNone(candidate["hearing_impaired"])
            self.assertFalse(candidate["hearing_impaired_verifiable"])
            self.assertIsNone(candidate["language"]["hi"])
            self.assertIsNone(candidate["language"]["forced"])

    def test_hearing_impaired_request_is_searched_as_the_base_language(self):
        server, base = self.serve(lambda record: _page([_record(5)]))
        hi_request = {"alpha3": "eng", "hi": True, "forced": False}
        (candidate,) = self.search([(base, "key-a")], languages=[hi_request])
        (request,) = server.requests
        self.assertEqual(request["query"]["language"], "en")
        self.assertEqual(candidate["language"]["alpha3"], "eng")
        self.assertIsNone(candidate["hearing_impaired"])

    def test_plain_and_hearing_impaired_requests_for_one_language_send_one_search(self):
        server, base = self.serve(lambda record: _page([_record(5)]))
        hi_request = {"alpha3": "eng", "hi": True, "forced": False}
        candidates = self.search([(base, "key-a")], languages=[ENGLISH, hi_request])
        self.assertEqual(len(candidates), 1)
        (request,) = server.requests
        self.assertEqual(request["query"]["language"], "en")


class ManifestTests(UNIT3DTestCase):
    def test_manifest_declares_the_settings_the_secret_and_the_languages(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        properties = manifest["config_schema"]["properties"]
        self.assertEqual(set(properties), {"endpoints", "api_keys", "match_files", "timeout_seconds"})
        self.assertEqual(manifest["secret_fields"], ["api_keys"])
        self.assertTrue(properties["api_keys"]["secret"])
        self.assertNotIn("secret", properties["endpoints"])
        self.assertEqual(properties["match_files"]["type"], "boolean")
        self.assertIs(properties["match_files"]["default"], True)
        timeout = properties["timeout_seconds"]
        self.assertEqual(
            (timeout["type"], timeout["default"], timeout["minimum"], timeout["maximum"]),
            ("integer", 30, 10, 60),
        )
        self.assertEqual(manifest["provider_id"], "unit3d")
        self.assertEqual(manifest["name"], "UNIT3D")
        self.assertTrue(hasattr(self.mod, manifest["entry_class"]))
        self.assertEqual(manifest["languages"], sorted(self.mod._LANGUAGE_CODES))
        self.assertEqual(manifest["source"]["path"], "providers/unit3d")


if __name__ == "__main__":
    unittest.main()
