"""Tests for the Bazarr+ Peer provider against a local mock of the Bazarr+ compat API.

Every test asserts on the requests the mock received, the candidates
returned, the exceptions raised and the log lines written. The clock and the
HTTP opener are seams: the test drives the clock so budget and cooldown
arithmetic is exact, and the opener records the timeout of every request.
"""

import base64
import hashlib
import http.server
import importlib.util
import io
import json
import os
import socket
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "bazarr_peer"
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
DISTRIBUTION_OFF = "Distribution Hub is off on this instance"
FILE_NAME = "Dune.2021.1080p.WEBRip.x264-GRP.srt"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("bazarr_peer_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROVIDER = _load_provider_module()
ALPHA3_ORDER = list(PROVIDER._LANGUAGE_CODES)


def _config(pairs, timeout_seconds=45, library_only=None, use_hash=None, include_ai_translated=None):
    config = {
        "endpoints": " ".join(url for url, _key in pairs),
        "api_keys": " ".join(key for _url, key in pairs),
        "timeout_seconds": timeout_seconds,
    }
    if library_only is not None:
        config["library_only"] = library_only
    if use_hash is not None:
        config["use_hash"] = use_hash
    if include_ai_translated is not None:
        config["include_ai_translated"] = include_ai_translated
    return config


def _tag(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _json(payload, status=200, headers=None):
    body = json.dumps(payload).encode("utf-8")
    return status, {"Content-Type": "application/json", **(headers or {})}, body


def _envelope(rows, total=None):
    return _json(
        {
            "total_pages": 1,
            "total_count": len(rows) if total is None else total,
            "per_page": 50,
            "page": 1,
            "data": rows,
        }
    )


def _throttled(retry_after=None):
    """A rate-limit refusal as the peer sends it: x-reason throttled and a reset time. It sets the cooldown."""
    headers = {"x-reason": "throttled"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return _json(
        {"message": "search rate limit exceeded (minute)", "reset_time_utc": "2026-01-01T00:00:00Z"},
        status=429,
        headers=headers,
    )


def _raw_error(status, body):
    """An error response with a chunked body. The body is written as given, so a test can end it early."""
    head = f"HTTP/1.1 {status} Error\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
    return head.encode("ascii") + body


def _row(number, file_id=None, **overrides):
    """A remote search row: a Bazarr+ subtitle record for a movie."""
    file_id = number * 10 if file_id is None else file_id
    attributes = {
        "subtitle_id": str(number),
        "language": "en",
        "release": "Dune.2021.1080p.WEBRip.x264-GRP",
        "comments": "Dune.2021.1080p.WEBRip.x264-GRP",
        "download_count": 3,
        "ratings": 0.0,
        "votes": 0,
        "from_trusted": False,
        "hd": False,
        "hearing_impaired": False,
        "moviehash_match": False,
        "ai_translated": False,
        "machine_translated": False,
        "foreign_parts_only": False,
        "fps": None,
        "upload_date": "2026-01-01T00:00:00Z",
        "uploader": {"name": "bazarr:uploader1"},
        "feature_details": {
            "feature_type": "Movie",
            "imdb_id": 1160419,
            "season_number": 0,
            "episode_number": 0,
            "title": "Dune",
            "movie_name": "Dune (2021)",
            "year": 2021,
        },
        "url": "",
        "files": [{"file_id": file_id, "file_name": FILE_NAME}],
    }
    attributes.update(overrides)
    return {"id": str(file_id), "type": "subtitle", "attributes": attributes}


def _episode_row(number, **overrides):
    """A remote search row for the Breaking Bad episode in EPISODE_VIDEO."""
    details = {
        "feature_type": "Episode",
        "imdb_id": 903747,
        "season_number": 1,
        "episode_number": 2,
        "title": "Pilot",
        "movie_name": "Breaking Bad",
        "year": 2008,
    }
    overrides.setdefault("feature_details", details)
    overrides.setdefault("files", [{"file_id": number * 10, "file_name": "breaking.bad.s01e02.720p.srt"}])
    return _row(number, **overrides)


def _local_row(file_id, stem="local-movie-1160419-en", name=FILE_NAME, **overrides):
    """A row for a subtitle stored on the peer, as the peer's local provider lists it."""
    attributes = {
        "subtitle_id": f"{stem}-{file_id}",
        "language": "en",
        "release": name,
        "comments": name,
        "download_count": 999999,
        "ratings": 10.0,
        "votes": 0,
        "from_trusted": False,
        "hd": False,
        "hearing_impaired": False,
        "moviehash_match": False,
        "ai_translated": False,
        "machine_translated": False,
        "foreign_parts_only": False,
        "fps": None,
        "upload_date": "2026-01-01T00:00:00Z",
        "uploader": {"name": "bazarr:local"},
        "feature_details": {
            "feature_type": "Movie",
            "imdb_id": 1160419,
            "season_number": 0,
            "episode_number": 0,
            "title": "Dune",
            "movie_name": "Dune (2021)",
            "year": 2021,
        },
        "url": "",
        "files": [{"file_id": file_id, "file_name": name}],
    }
    attributes.update(overrides)
    return {"id": f"subtitle-{file_id}", "type": "subtitle", "attributes": attributes}


def _stream_link(record, token="tok-1"):
    return f"http://{record['host']}/api/v1/download/stream/{token}"


def _login_response(record):
    return _json({"token": "tok-1", "status": 200, "base_url": record["host"], "user": {"id": 1}})


def _download_response(record, token="tok-1"):
    return _json(
        {
            "link": _stream_link(record, token),
            "remaining": 4,
            "remaining_downloads": 4,
            "reset_time_utc": "2026-01-01T00:00:00Z",
        }
    )


def _reference_hash(data):
    total = len(data)
    for index in range(0, 65536, 8):
        total += int.from_bytes(data[index:index + 8], "little")
    for index in range(len(data) - 65536, len(data), 8):
        total += int.from_bytes(data[index:index + 8], "little")
    return f"{total % 2 ** 64:016x}"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Handler(http.server.BaseHTTPRequestHandler):
    def _handle(self, method):
        parts = urllib.parse.urlsplit(self.path)
        query = {
            name: values[-1]
            for name, values in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()
        }
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        record = {
            "method": method,
            "path": parts.path,
            "query": query,
            "api_key": self.headers.get("Api-Key"),
            "auth": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "host": self.headers.get("Host"),
            "body": body,
        }
        with self.server.lock:
            self.server.requests.append(record)
        reply = self.server.respond(record)
        if isinstance(reply, bytes):
            # Raw bytes go out unchanged, and the connection closes after them, so a body can end early.
            self.close_connection = True
            self.wfile.write(reply)
            return
        status, headers, payload = reply
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, *args):
        del args


class _Upstream(http.server.ThreadingHTTPServer):
    def __init__(self, respond):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.respond = respond
        self.requests = []
        self.lock = threading.Lock()


class _SlowDripHandler(http.server.BaseHTTPRequestHandler):
    """Sends the headers of a 40 byte body, then holds the body until the test sets release.

    The server sets its started event once the headers are out. The hold ends when release is set,
    or after ten seconds, so a test that never sets it cannot block the server thread for long.
    """

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "40")
        self.end_headers()
        self.server.started.set()
        # The hold waits on an event, not on a timer, so the test controls when it ends.
        self.server.release.wait(10)
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


class _ClockedResponse(_ClockedBody):
    """A whole 200 response: the body is read in chunks, and each read moves the clock on."""

    headers = {"Content-Type": "application/json"}

    def __init__(self, payload, clock, step):
        super().__init__([payload[index:index + 16] for index in range(0, len(payload), 16)], clock, step)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        del exc_info
        return False

    def getcode(self):
        return 200


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


class PeerTestCase(unittest.TestCase):
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

    def search(self, pairs, video=None, languages=None, provider=None, timeout_seconds=45, **options):
        provider = provider or self.mod.BazarrPeerProvider()
        return provider.search(
            video=dict(MOVIE_VIDEO if video is None else video),
            languages=[ENGLISH] if languages is None else list(languages),
            config=_config(pairs, timeout_seconds=timeout_seconds, **options),
        )

    def searches(self, server):
        """The search requests the peer received. The library probe is a separate route."""
        return [record for record in server.requests if record["path"] == "/api/v1/subtitles"]

    def routes(self, search=None, providers=None, login=None, download=None, stream=None, prefix=""):
        """A peer whose routes answer from the given functions. Unlisted routes answer 404."""

        def respond(record):
            path = record["path"]
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            if path == "/api/v1/subtitles" and search is not None:
                return search(record)
            if path == "/api/v1/providers" and providers is not None:
                return providers(record)
            if path == "/api/v1/login" and login is not None:
                return login(record)
            if path == "/api/v1/download" and download is not None:
                return download(record)
            if path.startswith("/api/v1/download/stream/") and stream is not None:
                return stream(record)
            return _json({"message": "Not Found"}, status=404)

        return self.serve(respond)


class ConfigurationTests(PeerTestCase):
    def test_zero_endpoints_is_a_configuration_error(self):
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([])

    def test_endpoint_and_key_counts_must_match(self):
        config = {
            "endpoints": "https://a.example https://b.example",
            "api_keys": "key-a",
            "timeout_seconds": 45,
        }
        with self.assertRaisesRegex(self.mod.ConfigurationError, r"2 endpoint\(s\) but 1 API key\(s\)") as caught:
            self.mod.BazarrPeerProvider().search(video=dict(MOVIE_VIDEO), languages=[ENGLISH], config=config)
        self.assertNotIn("key-a", str(caught.exception))

    def test_invalid_endpoints_are_rejected(self):
        for url in (
            "ftp://peer.example",
            "https://",
            "https://user:secret@peer.example",
            "https://peer.example/?key=1",
            "https://peer.example/#fragment",
            "https://peer.example:99999",
            "http://[::1",
        ):
            with self.subTest(url=url), self.assertRaises(self.mod.ConfigurationError):
                self.search([(url, "key-a")])

    def test_duplicate_normalized_endpoints_are_rejected(self):
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([("https://Peer.example/", "key-a"), ("https://peer.example", "key-b")])

    def test_timeout_must_be_a_whole_number_between_thirty_and_ninety(self):
        for value in (29, 91, "soon", "9" * 5000):
            with self.subTest(value=value), self.assertRaises(self.mod.ConfigurationError):
                self.search([("https://peer.example", "key-a")], timeout_seconds=value)
        _server, base = self.serve(lambda record: _envelope([]))
        self.assertEqual(self.search([(base, "key-a")], timeout_seconds=30), [])
        self.assertEqual(self.search([(base, "key-a")], timeout_seconds=90), [])

    def test_non_ascii_api_key_is_rejected_without_echoing_it(self):
        _server, base = self.serve(lambda record: _envelope([]))
        with self.assertRaises(self.mod.ConfigurationError) as caught:
            self.search([(base, "key-SECRET-\u00e9")])
        message = str(caught.exception)
        self.assertIn("endpoint 1", message)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("\u00e9", message)

    def test_non_ascii_endpoint_path_is_rejected_without_echoing_it(self):
        _server, base = self.serve(lambda record: _envelope([]))
        with self.assertRaises(self.mod.ConfigurationError) as caught:
            self.search([(f"{base}/track\u00e9r", "key-a")])
        message = str(caught.exception)
        self.assertIn("endpoint 1", message)
        self.assertNotIn("track", message)
        self.assertNotIn("\u00e9", message)

    def test_opensubtitles_service_is_refused_before_any_request(self):
        for url in ("https://api.opensubtitles.com", "https://sub.api.opensubtitles.com/peer"):
            with self.subTest(url=url), self.assertRaisesRegex(
                self.mod.ConfigurationError, "this is the OpenSubtitles service"
            ):
                self.search([("https://peer.example", "key-a"), (url, "key-b")])
        self.assertEqual(self.timeouts, [])

    def test_more_than_sixteen_endpoints_keep_the_first_sixteen_and_log_the_drop(self):
        def respond(record):
            number = int(record["path"].split("/t", 1)[1].split("/", 1)[0])
            return _envelope([_row(number + 1)])

        server, base = self.serve(respond)
        pairs = [(f"{base}/t{number}", f"key-{number}") for number in range(17)]
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            results = self.search(pairs)
        self.assertEqual(len(results), 16)
        self.assertEqual(
            {record["path"] for record in server.requests},
            {f"/t{number}/api/v1/subtitles" for number in range(16)},
        )
        self.assertEqual(len(logs.output), 1)
        self.assertIn("16", "\n".join(logs.output))

    def test_a_duplicate_endpoint_past_the_sixteenth_is_still_refused(self):
        pairs = [(f"https://peer{number}.example", f"key-{number}") for number in range(16)]
        pairs.append(("https://Peer0.example/", "key-17"))
        with self.assertRaisesRegex(self.mod.ConfigurationError, "endpoint 17 duplicates"):
            self.search(pairs)

    def test_an_invalid_key_past_the_sixteenth_endpoint_is_still_refused(self):
        pairs = [(f"https://peer{number}.example", f"key-{number}") for number in range(16)]
        pairs.append(("https://peer16.example", "bad\u00e9key"))
        with self.assertRaisesRegex(self.mod.ConfigurationError, "endpoint 17 has an API key"):
            self.search(pairs)

    def test_non_boolean_setting_falls_back_to_its_default(self):
        pairs = [("https://peer.example", "key-a")]
        self.assertTrue(self.mod._options(_config(pairs, library_only="false")).library_only)
        self.assertFalse(self.mod._options(_config(pairs, library_only=False)).library_only)


class SearchRequestTests(PeerTestCase):
    def test_movie_search_sends_identity_scope_budget_and_paging(self):
        server, base = self.serve(lambda record: _envelope([]))
        self.search([(base, "key-a")])
        (request,) = self.searches(server)
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/api/v1/subtitles")
        self.assertEqual(request["api_key"], "key-a")
        self.assertIsNone(request["auth"])
        self.assertEqual(
            request["query"],
            {
                "type": "movie",
                "imdb_id": "tt1160419",
                "languages": "en",
                "exclude_providers": "bazarr_peer",
                "only_providers": "local",
                "moviebytesize": "4321",
                "timeout_seconds": "45",
                "page": "1",
            },
        )

    def test_episode_search_uses_the_series_imdb_id_and_the_season_and_episode(self):
        server, base = self.serve(lambda record: _envelope([]))
        self.search([(base, "key-a")], video=EPISODE_VIDEO)
        (request,) = self.searches(server)
        self.assertEqual(
            request["query"],
            {
                "type": "episode",
                "season_number": "1",
                "episode_number": "2",
                "imdb_id": "tt0903747",
                "languages": "en",
                "exclude_providers": "bazarr_peer",
                "only_providers": "local",
                "moviebytesize": "900",
                "timeout_seconds": "45",
                "page": "1",
            },
        )

    def test_library_only_off_sends_no_only_providers_parameter(self):
        server, base = self.serve(lambda record: _envelope([]))
        self.search([(base, "key-a")], library_only=False)
        (request,) = server.requests
        self.assertNotIn("only_providers", request["query"])
        self.assertEqual(request["query"]["exclude_providers"], "bazarr_peer")

    def test_use_hash_off_sends_no_hash_or_size(self):
        server, base = self.serve(lambda record: _envelope([]))
        video = dict(MOVIE_VIDEO, hashes={"opensubtitles": "8e245d9679d31e12"})
        self.search([(base, "key-a")], video=video, use_hash=False)
        (request,) = self.searches(server)
        self.assertNotIn("moviehash", request["query"])
        self.assertNotIn("moviebytesize", request["query"])

    def test_hash_from_the_payload_is_sent_and_the_size_follows_it(self):
        server, base = self.serve(lambda record: _envelope([]))
        video = dict(MOVIE_VIDEO, hashes={"napisy24": "8e245d9679d31e12"})
        self.search([(base, "key-a")], video=video)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["moviehash"], "8e245d9679d31e12")
        self.assertEqual(request["query"]["moviebytesize"], "4321")

    def test_worker_hash_is_computed_from_the_file_when_the_payload_has_none(self):
        data = bytes(range(256)) * 1024
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Dune.2021.mkv")
            with open(path, "wb") as handle:
                handle.write(data)
            server, base = self.serve(lambda record: _envelope([]))
            video = dict(MOVIE_VIDEO, original_path=path)
            self.search([(base, "key-a")], video=video)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["moviehash"], _reference_hash(data))

    def test_hash_search_sends_the_file_size_when_the_host_gives_none(self):
        data = bytes(range(256)) * 1024
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Dune.2021.mkv")
            with open(path, "wb") as handle:
                handle.write(data)
            server, base = self.serve(lambda record: _envelope([]))
            video = dict(MOVIE_VIDEO, size=None, original_path=path)
            self.search([(base, "key-a")], video=video)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["moviehash"], _reference_hash(data))
        self.assertEqual(request["query"]["moviebytesize"], str(len(data)))

    def test_small_file_sends_no_hash_and_raises_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            small = os.path.join(directory, "small.mkv")
            with open(small, "wb") as handle:
                handle.write(b"x" * (100 * 1024))
            server, base = self.serve(lambda record: _envelope([]))
            results = self.search([(base, "key-a")], video=dict(MOVIE_VIDEO, original_path=small))
        self.assertEqual(results, [])
        (request,) = self.searches(server)
        self.assertNotIn("moviehash", request["query"])

    def test_unreadable_file_sends_no_hash_logs_the_exception_class_and_raises_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "missing.mkv")
            server, base = self.serve(lambda record: _envelope([]))
            with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                results = self.search([(base, "key-a")], video=dict(MOVIE_VIDEO, original_path=missing))
        self.assertEqual(results, [])
        (request,) = self.searches(server)
        self.assertNotIn("moviehash", request["query"])
        text = "\n".join(logs.output)
        self.assertIn("FileNotFoundError", text)
        self.assertNotIn(missing, text)

    def test_tmdb_only_movie_sends_tmdb_id_and_no_imdb_id(self):
        server, base = self.serve(lambda record: _envelope([]))
        video = {"kind": "movie", "title": "Dune", "year": 2021, "tmdb_id": 438631, "size": 4321}
        self.search([(base, "key-a")], video=video)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["tmdb_id"], "438631")
        self.assertNotIn("imdb_id", request["query"])
        self.assertNotIn("query", request["query"])

    def test_tmdb_only_episode_sends_the_file_name_as_query(self):
        server, base = self.serve(lambda record: _envelope([]))
        video = dict(EPISODE_VIDEO, series_imdb_id=None, tmdb_id=1396)
        self.search([(base, "key-a")], video=video)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["tmdb_id"], "1396")
        self.assertEqual(request["query"]["query"], "breaking.bad.s01e02.720p.mkv")
        self.assertNotIn("imdb_id", request["query"])

    def test_episode_without_series_or_tmdb_id_sends_no_file_name_query(self):
        server, base = self.serve(lambda record: _envelope([_episode_row(1)]))
        video = dict(EPISODE_VIDEO, series_imdb_id=None, tmdb_id=None)
        with self.assertLogs("bazarr_peer", level="INFO"):
            self.assertEqual(self.search([(base, "key-a")], video=video), [])
        self.assertEqual(server.requests, [])

    def test_regional_portuguese_is_requested_as_pt_dash_br_and_plain_portuguese_as_pt(self):
        server, base = self.serve(lambda record: _envelope([]))
        regional = {"alpha3": "por", "country_alpha2": "BR", "hi": False, "forced": False}
        self.search([(base, "key-a")], languages=[regional])
        self.search([(base, "key-a")], languages=[{"alpha3": "por", "hi": False, "forced": False}])
        self.assertEqual([request["query"]["languages"] for request in self.searches(server)], ["pt-BR", "pt"])

    def test_forced_only_requests_and_unsupported_media_send_nothing(self):
        server, base = self.serve(lambda record: _envelope([]))
        forced = {"alpha3": "eng", "hi": False, "forced": True}
        self.assertEqual(self.search([(base, "key-a")], languages=[forced]), [])
        self.assertEqual(self.search([(base, "key-a")], video={"kind": "series", "title": "X"}), [])
        self.assertEqual(server.requests, [])

    def test_imdb_id_is_sent_as_tt_and_seven_digits(self):
        server, base = self.serve(lambda record: _envelope([]))
        self.search([(base, "key-a")], video=dict(MOVIE_VIDEO, imdb_id="42"))
        self.search([(base, "key-a")], video=dict(MOVIE_VIDEO, imdb_id="tt0000042"))
        self.assertEqual([request["query"]["imdb_id"] for request in self.searches(server)], ["tt0000042"] * 2)

    def test_all_requested_codes_go_in_one_comma_separated_request(self):
        server, base = self.serve(lambda record: _envelope([]))
        alpha3_codes = ALPHA3_ORDER[:60]
        alpha2_codes = [self.mod._LANGUAGE_CODES[code] for code in alpha3_codes]
        languages = [{"alpha3": code, "hi": False, "forced": False} for code in alpha3_codes]
        self.search([(base, "key-a")], languages=languages, library_only=False)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["languages"], ",".join(alpha2_codes))
        self.assertEqual(self.timeouts, [45])

    def test_one_language_search_sends_exactly_one_request_per_endpoint_with_page_one(self):
        rows = [_row(number) for number in (1, 2, 3)]
        server_a, base_a = self.serve(lambda record: _envelope(rows))
        server_b, base_b = self.serve(lambda record: _envelope(rows))
        self.search([(base_a, "key-a"), (base_b, "key-b")])
        for server in (server_a, server_b):
            (request,) = self.searches(server)
            self.assertEqual(request["query"]["page"], "1")
            self.assertNotIn("per_page", request["query"])

    def test_unencodable_name_without_other_ids_sends_no_request_and_does_not_cool_the_endpoint(self):
        server, base = self.serve(lambda record: _envelope([_row(1)]))
        video = dict(EPISODE_VIDEO, series_imdb_id=None, tmdb_id=None, original_name="Show\udcff.mkv")
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="INFO"):
            self.assertEqual(self.search([(base, "key-a")], video=video, provider=provider), [])
        self.assertEqual(server.requests, [])
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual(len(self.search([(base, "key-a")], provider=provider)), 1)
        self.assertEqual(len(server.requests), 1)

    def test_unencodable_name_is_left_out_of_a_tmdb_only_search_without_cooling_the_endpoint(self):
        server, base = self.serve(lambda record: _envelope([_episode_row(1)]))
        video = dict(EPISODE_VIDEO, series_imdb_id=None, tmdb_id=1396, original_name="Show\udcff.mkv")
        provider = self.mod.BazarrPeerProvider()
        self.assertEqual(len(self.search([(base, "key-a")], video=video, provider=provider)), 1)
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["tmdb_id"], "1396")
        self.assertNotIn("query", request["query"])
        self.assertEqual(provider._cooldowns, {})


class BudgetTests(PeerTestCase):
    def test_timeout_seconds_is_the_remaining_budget_rounded_up_to_whole_seconds(self):
        server, base = self.serve(lambda record: _envelope([_row(1)]))
        endpoint = self.mod._configured_endpoints(_config([(base, "key-a")]))[0]
        options = self.mod._options(_config([(base, "key-a")]))
        video = dict(MOVIE_VIDEO)
        identity = self.mod._identity(video, options)
        provider = self.mod.BazarrPeerProvider()
        for remaining in (43.5, 1.5, 0.5):
            provider._search_endpoint(endpoint, video, identity, "en", {"eng"}, options, self.clock.now + remaining)
        self.assertEqual(
            [request["query"]["timeout_seconds"] for request in self.searches(server)], ["44", "2", "1"]
        )

    def test_timeout_never_exceeds_ninety_seconds(self):
        server, base = self.serve(lambda record: _envelope([]))
        self.search([(base, "key-a")], timeout_seconds=90, library_only=False)
        (request,) = server.requests
        self.assertEqual(request["query"]["timeout_seconds"], "90")
        self.assertEqual(self.timeouts, [90])

    def test_budget_spent_while_the_search_is_read_keeps_its_candidates(self):
        original_parse = self.mod._parse_search

        def parse_and_spend_budget(body):
            # The budget runs out once the response has arrived in full, so its body read is not cut short.
            parsed = original_parse(body)
            self.clock.now += 50
            return parsed

        server, base = self.serve(lambda record: _envelope([_row(1)]))
        with patch.object(self.mod, "_parse_search", parse_and_spend_budget):
            results = self.search([(base, "key-a")])
        self.assertEqual(len(server.requests), 1)
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])

    def test_budget_spent_before_the_first_request_sends_nothing_and_does_not_cool(self):
        server, base = self.serve(lambda record: _envelope([_row(1)]))
        endpoint = self.mod._configured_endpoints(_config([(base, "key-a")]))[0]
        options = self.mod._options(_config([(base, "key-a")]))
        video = dict(MOVIE_VIDEO)
        identity = self.mod._identity(video, options)
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            outcome = provider._search_endpoint(endpoint, video, identity, "en", {"eng"}, options, self.clock.now)
        self.assertIn("budget_spent", "\n".join(logs.output))
        self.assertTrue(outcome.failed)
        self.assertIsInstance(outcome.error, self.mod.ServiceUnavailable)
        self.assertEqual(str(outcome.error), "the search budget was spent before this endpoint was queried")
        self.assertEqual(server.requests, [])
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual(len(self.search([(base, "key-a")], provider=provider)), 1)
        self.assertEqual(len(server.requests), 1)

    def test_slow_peer_fails_at_the_budget_with_an_injected_clock(self):
        body = json.dumps({"total_count": 1, "data": [_row(1)]}).encode("utf-8")

        def open_slow_peer(fullurl, data=None, timeout=None):
            self.timeouts.append(timeout)
            return _ClockedResponse(body, self.clock, step=50)

        with patch.object(self.mod._OPENER, "open", open_slow_peer):
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.search([("https://slow.example", "key-a")])
        self.assertEqual(self.timeouts, [45])


class FailureIsolationTests(PeerTestCase):
    def test_failed_endpoint_does_not_stop_a_healthy_endpoint(self):
        _sick, sick_base = self.serve(lambda record: _json({"message": "down"}, status=500))
        _well, well_base = self.serve(lambda record: _envelope([_row(5)]))
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            results = self.search([(sick_base, "key-sick"), (well_base, "key-well")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(well_base)}:5"])
        text = "\n".join(logs.output)
        self.assertIn(_tag(sick_base), text)
        self.assertIn("ServiceUnavailable", text)
        self.assertNotIn("key-sick", text)

    def test_rate_limited_endpoint_does_not_stop_a_healthy_endpoint(self):
        throttled, base_a = self.serve(lambda record: _throttled("120"))
        healthy, base_b = self.serve(lambda record: _envelope([_row(5)]))
        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.BazarrPeerProvider()
        results = self.search(pairs, provider=provider)
        self.assertEqual(len(throttled.requests), 1)
        self.assertEqual(len(healthy.requests), 1)
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base_b)}:5"])
        again = self.search(pairs, provider=provider)
        self.assertEqual([candidate["id"] for candidate in again], [f"{_tag(base_b)}:5"])
        self.assertEqual(len(throttled.requests), 1)
        self.assertEqual(len(healthy.requests), 2)

    def test_every_endpoint_unauthorized_raises_authentication_error(self):
        _a, base_a = self.serve(
            lambda record: _json({"message": "Invalid API key"}, status=403, headers={"x-reason": "auth"})
        )
        _b, base_b = self.serve(
            lambda record: _json({"message": "Invalid API key"}, status=401, headers={"x-reason": "auth"})
        )
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base_a, "key-a"), (base_b, "key-b")])

    def test_every_endpoint_rate_limited_raises_api_throttled_with_the_longest_retry_after(self):
        _a, base_a = self.serve(lambda record: _throttled("120"))
        _b, base_b = self.serve(lambda record: _throttled("90"))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base_a, "key-a"), (base_b, "key-b")])
        self.assertEqual(caught.exception.retry_after, 120)

    def test_mixed_failures_raise_service_unavailable(self):
        _a, base_a = self.serve(lambda record: _json({}, status=403))
        _b, base_b = self.serve(lambda record: _json({}, status=500))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base_a, "key-a"), (base_b, "key-b")])

    def test_bad_request_is_a_configuration_error(self):
        _server, base = self.serve(
            lambda record: _json({"message": "invalid language code"}, status=400, headers={"x-reason": "bad-request"})
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, "bad_request"):
            self.search([(base, "key-a")])

    def test_compat_disabled_503_body_is_a_configuration_error(self):
        # Runtime disable answers 503 with the "error" key and no x-reason header.
        # The search raises a configuration error that reads "Distribution Hub is off on this instance".
        _server, base = self.serve(lambda record: _json({"error": "compat endpoint disabled"}, status=503))
        with self.assertRaisesRegex(self.mod.ConfigurationError, DISTRIBUTION_OFF):
            self.search([(base, "key-a")])

    def test_other_503_is_transient_and_not_a_configuration_error(self):
        _server, base = self.serve(
            lambda record: _json({"message": "provider fetch failed"}, status=503, headers={"x-reason": "upstream"})
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs, self.assertRaises(
            self.mod.ServiceUnavailable
        ) as caught:
            self.search([(base, "key-a")])
        self.assertNotIsInstance(caught.exception, self.mod.ConfigurationError)
        self.assertIn("peer_unavailable", "\n".join(logs.output))

    def test_redirects_are_refused_and_the_key_is_not_resent(self):
        target, target_base = self.serve(lambda record: _envelope([_row(1)]))
        _redirector, base = self.serve(lambda record: (302, {"Location": f"{target_base}/api/v1/subtitles"}, b""))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])
        self.assertEqual(target.requests, [])

    def test_tls_handshake_failure_is_a_configuration_error(self):
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
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([(base, "key-a")])

    def test_tls_error_while_reading_a_body_is_a_service_unavailable(self):
        base = "https://peer.example"
        endpoint = self.mod._Endpoint(_tag(base), base, "peer.example", "key-a")
        with patch.object(self.mod._OPENER, "open", lambda *args, **kwargs: _TLSDroppingBody()):
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.mod._fetch(endpoint, "/api/v1/subtitles", None, 10, self.mod.MAX_SEARCH_BYTES, self.clock.now + 30)

    def test_unreachable_endpoint_is_service_unavailable(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(f"http://127.0.0.1:{port}", "key-a")])

    def test_non_json_search_response_is_an_endpoint_failure_not_an_empty_result(self):
        _bad, bad_base = self.serve(lambda record: (200, {"Content-Type": "text/html"}, b"<html>oops</html>"))
        _well, well_base = self.serve(lambda record: _envelope([_row(3)]))
        results = self.search([(bad_base, "key-bad"), (well_base, "key-well")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(well_base)}:3"])
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(bad_base, "key-bad")])

    def test_valid_empty_results_are_a_success(self):
        _a, base_a = self.serve(lambda record: _envelope([]))
        _b, base_b = self.serve(lambda record: _envelope([]))
        self.assertEqual(self.search([(base_a, "key-a"), (base_b, "key-b")]), [])

    def test_failure_log_never_carries_the_api_key_or_the_response_body(self):
        for status in (400, 403, 500, 503):
            with self.subTest(status=status):
                marker = f"SECRET-{status}"
                _sick, sick_base = self.serve(
                    lambda record, status=status, marker=marker: _json(
                        {"error": f"{marker}-ERROR", "message": f"{marker}-MESSAGE"}, status=status
                    )
                )
                with self.assertLogs("bazarr_peer", level="DEBUG") as logs:
                    with self.assertRaises(
                        (self.mod.ConfigurationError, self.mod.AuthenticationError, self.mod.ServiceUnavailable)
                    ) as caught:
                        self.search([(sick_base, "key-sick-value")])
                text = "\n".join(logs.output)
                self.assertNotIn(marker, text)
                self.assertNotIn(marker, str(caught.exception))
                self.assertNotIn("key-sick-value", text)

    def test_error_detail_keeps_at_most_the_detail_cap_of_a_long_error_value(self):
        body = json.dumps({"error": "a" * 300 + "TAIL-MARKER"}).encode("utf-8")
        self.assertLess(len(body), self.mod.MAX_ERROR_BYTES)
        key, detail = self.mod._error_detail(io.BytesIO(body))
        self.assertEqual(key, "error")
        self.assertLessEqual(len(detail), self.mod.DETAIL_MAX_CHARS)
        self.assertEqual(detail, "a" * self.mod.DETAIL_MAX_CHARS)
        self.assertNotIn("TAIL-MARKER", detail)

    def test_cut_chunked_error_body_on_search_is_an_endpoint_failure_with_a_cooldown(self):
        for label, body in (
            ("chunk ends early", b'24\r\n{"error": "cut sh'),
            ("chunk size line too long", b"x" * 65537),
        ):
            with self.subTest(label):
                server, base = self.serve(lambda record, body=body: _raw_error(500, body))
                provider = self.mod.BazarrPeerProvider()
                with self.assertLogs("bazarr_peer", level="WARNING"):
                    with self.assertRaises(self.mod.ServiceUnavailable):
                        self.search([(base, "key-a")], provider=provider)
                ((cooled_until, _error),) = provider._cooldowns.values()
                self.assertEqual(cooled_until - self.clock.now, 60)
                with self.assertRaises(self.mod.ServiceUnavailable):
                    self.search([(base, "key-a")], provider=provider)
                self.assertEqual(len(self.searches(server)), 1)

    def test_unexpected_error_in_one_endpoint_keeps_the_other_endpoints_candidates(self):
        _bad, bad_base = self.serve(lambda record: _envelope([_row(1)]))
        _well, well_base = self.serve(lambda record: _envelope([_row(5)]))
        bad_tag = _tag(bad_base)
        original_candidate = self.mod._candidate

        def failing_candidate(video, endpoint, row, requested, options, identity):
            if endpoint.tag == bad_tag:
                raise RuntimeError("SECRET-MESSAGE")
            return original_candidate(video, endpoint, row, requested, options, identity)

        with patch.object(self.mod, "_candidate", failing_candidate):
            with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                results = self.search([(bad_base, "key-bad"), (well_base, "key-well")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(well_base)}:5"])
        text = "\n".join(logs.output)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SECRET-MESSAGE", text)

    def test_unexpected_error_outside_the_search_fails_the_endpoint_without_a_cooldown(self):
        base = "https://peer.example"
        endpoint = self.mod._configured_endpoints(_config([(base, "key-a")]))[0]
        options = self.mod._options(_config([(base, "key-a")]))
        video = dict(MOVIE_VIDEO)
        identity = self.mod._identity(video, options)
        deadline = self.clock.now + 45
        provider = self.mod.BazarrPeerProvider()
        with patch.object(
            self.mod.BazarrPeerProvider, "_search_endpoint", side_effect=RuntimeError("SECRET-MESSAGE")
        ):
            with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                outcome = provider._search_contained(
                    endpoint, video, identity, "en", {"eng"}, options, deadline
                )
        self.assertEqual([record.levelname for record in logs.records], ["WARNING"])
        text = "\n".join(logs.output)
        self.assertIn(_tag(base), text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SECRET-MESSAGE", text)
        self.assertEqual(outcome.candidates, [])
        self.assertIsInstance(outcome.error, self.mod.ServiceUnavailable)
        self.assertEqual(str(outcome.error), "the endpoint failed unexpectedly")
        self.assertEqual(provider._cooldowns, {})

    def test_deeply_nested_search_response_is_a_service_unavailable(self):
        nested = b"[" * 100000 + b"]" * 100000
        _server, base = self.serve(lambda record: (200, {"Content-Type": "application/json"}, nested))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])

    def test_huge_total_count_digit_string_is_treated_as_absent(self):
        payload = {"total_pages": 1, "total_count": "9" * 5000, "per_page": 50, "page": 1, "data": [_row(1)]}
        _server, base = self.serve(lambda record: _json(payload))
        results = self.search([(base, "key-a")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:1"])

    def test_results_beyond_the_returned_page_are_logged_as_not_fetched(self):
        server, base = self.serve(lambda record: _envelope([_row(1)], total=60))
        with self.assertLogs("bazarr_peer", level="INFO") as logs:
            results = self.search([(base, "key-a")])
        self.assertEqual(len(results), 1)
        self.assertIn("more_results_not_fetched", "\n".join(logs.output))
        (request,) = self.searches(server)
        self.assertEqual(request["query"]["page"], "1")


class CompatibilityTests(PeerTestCase):
    def test_search_404_with_the_compat_disabled_header_is_a_configuration_error_without_a_probe(self):
        server, base = self.routes(
            search=lambda record: _json({"message": "disabled"}, status=404, headers={"x-reason": "compat-disabled"})
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, DISTRIBUTION_OFF):
            self.search([(base, "key-a")])
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/subtitles"])

    def test_probe_404_with_the_compat_disabled_header_is_distribution_hub_off(self):
        _server, base = self.routes(
            search=lambda record: _json({"message": "Not Found"}, status=404),
            providers=lambda record: _json(
                {"message": "disabled"}, status=404, headers={"x-reason": "compat-disabled"}
            ),
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, DISTRIBUTION_OFF):
            self.search([(base, "key-a")])

    def test_probe_401_is_an_authentication_error(self):
        _server, base = self.routes(
            search=lambda record: _json({}, status=404),
            providers=lambda record: _json({"message": "Token invalid or expired"}, status=401),
        )
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")])

    def test_redirect_on_the_probe_after_a_missing_route_is_a_transient_endpoint_failure(self):
        target, target_base = self.serve(lambda record: _envelope([]))
        _server, base = self.routes(
            search=lambda record: _json({"message": "Not Found"}, status=404),
            providers=lambda record: (302, {"Location": f"{target_base}/api/v1/providers"}, b""),
        )
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.search([(base, "key-a")])
        self.assertEqual(target.requests, [])

    def test_search_404_without_the_header_probes_the_providers_route(self):
        server, base = self.routes(
            search=lambda record: _json({"message": "Not Found"}, status=404),
            providers=lambda record: _json({"data": [{"name": "Subs"}]}),
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, "subtitles route is missing"):
            self.search([(base, "key-a")])
        self.assertEqual(
            [request["path"] for request in server.requests],
            ["/api/v1/subtitles", "/api/v1/providers"],
        )

    def test_failed_route_probe_with_a_tls_error_is_a_configuration_error(self):
        _server, base = self.routes(
            search=lambda record: _json({"message": "Not Found"}, status=404),
            providers=lambda record: _json({"data": [{"name": "Subs"}]}),
        )
        provider = self.mod.BazarrPeerProvider()
        tls_error = self.mod.ConfigurationError("the TLS handshake with the endpoint failed")
        with patch.object(self.mod.BazarrPeerProvider, "_provider_names", side_effect=tls_error):
            with self.assertLogs("bazarr_peer", level="WARNING"), self.assertRaisesRegex(
                self.mod.ConfigurationError, "the TLS handshake with the endpoint failed"
            ):
                self.search([(base, "key-a")], provider=provider)
        ((endpoint_key, (cooled_until, error)),) = provider._cooldowns.items()
        self.assertEqual(endpoint_key[0], _tag(base))
        self.assertIsInstance(error, self.mod.ConfigurationError)
        self.assertEqual(cooled_until - self.clock.now, 60)

    def test_probe_404_means_the_address_is_not_a_bazarr_plus_instance(self):
        _server, base = self.routes(
            search=lambda record: _json({}, status=404), providers=lambda record: _json({}, status=404)
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, "check the address"):
            self.search([(base, "key-a")])

    def test_probe_403_is_an_authentication_error(self):
        _server, base = self.routes(
            search=lambda record: _json({}, status=404), providers=lambda record: _json({}, status=403)
        )
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")])

    def test_probe_503_with_the_compat_body_is_distribution_hub_off(self):
        _server, base = self.routes(
            search=lambda record: _json({}, status=404),
            providers=lambda record: _json({"error": "compat endpoint disabled"}, status=503),
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, DISTRIBUTION_OFF):
            self.search([(base, "key-a")])

    def test_transient_probe_503_is_not_a_configuration_error(self):
        _server, base = self.routes(
            search=lambda record: _json({}, status=404),
            providers=lambda record: _json({"message": "busy"}, status=503),
        )
        with self.assertRaises(self.mod.ServiceUnavailable) as caught:
            self.search([(base, "key-a")])
        self.assertNotIsInstance(caught.exception, self.mod.ConfigurationError)

    def test_search_403_never_triggers_a_login(self):
        server, base = self.routes(
            search=lambda record: _json({"message": "Invalid API key"}, status=403, headers={"x-reason": "auth"}),
            login=_login_response,
        )
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")])
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/subtitles"])

    def test_boot_time_disabled_stub_answers_every_path_with_a_configuration_error(self):
        def respond(record):
            return _json({"message": "disabled"}, status=404, headers={"x-reason": "compat-disabled"})

        _server, base = self.serve(respond)
        with self.assertRaisesRegex(self.mod.ConfigurationError, DISTRIBUTION_OFF):
            self.search([(base, "key-a")])


class CooldownTests(PeerTestCase):
    def test_rate_limit_without_retry_after_skips_the_endpoint_for_sixty_seconds(self):
        throttled, base_a = self.serve(lambda record: _throttled())
        _well, base_b = self.serve(lambda record: _envelope([_row(4)]))
        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.BazarrPeerProvider()
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 59
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 2
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 2)

    def test_retry_after_is_honored_even_when_another_endpoint_succeeds(self):
        throttled, base_a = self.serve(lambda record: _throttled("120"))
        _well, base_b = self.serve(lambda record: _envelope([_row(4)]))
        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.BazarrPeerProvider()
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.clock.now += 119
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 2
        self.assertEqual(len(self.search(pairs, provider=provider)), 1)
        self.assertEqual(len(throttled.requests), 2)

    def test_http_date_retry_after_falls_back_to_sixty_seconds(self):
        throttled, base = self.serve(lambda record: _throttled("Wed, 21 Oct 2026 07:28:00 GMT"))
        provider = self.mod.BazarrPeerProvider()
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.clock.now += 59
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(throttled.requests), 1)

    def test_retry_after_is_capped_at_one_day(self):
        throttled, base = self.serve(lambda record: _throttled("999999"))
        provider = self.mod.BazarrPeerProvider()
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.clock.now += 86399
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(throttled.requests), 1)
        self.clock.now += 1
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(throttled.requests), 2)

    def test_rate_limited_search_raises_api_throttled_and_the_replay_sends_no_request(self):
        throttled, base = self.serve(lambda record: _throttled("120"))
        provider = self.mod.BazarrPeerProvider()
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(throttled.requests), 1)
        self.assertEqual(caught.exception.retry_after, 120)

    def test_rate_limit_reports_the_cooldown_in_force_when_a_longer_one_is_kept(self):
        _server, base = self.serve(lambda record: _throttled("60"))
        endpoint = self.mod._configured_endpoints(_config([(base, "key-a")]))[0]
        provider = self.mod.BazarrPeerProvider()
        provider._cool_down(endpoint, self.mod.APIThrottled("the peer is rate limiting searches"), 3600)
        with self.assertRaises(self.mod._Refused) as caught:
            self.mod._fetch(endpoint, "/api/v1/subtitles", None, 45, self.mod.MAX_SEARCH_BYTES, self.clock.now + 45)
        error = provider._refuse(endpoint, caught.exception)
        self.assertEqual(error.retry_after, 3600)
        self.assertEqual(provider._cooldowns[self.mod._credential_key(endpoint)][0], self.clock.now + 3600)

    def test_timed_out_endpoint_is_skipped_for_sixty_seconds_then_queried_again(self):
        _healthy, base_b = self.serve(lambda record: _envelope([_row(4)]))
        base_a = "https://timeout.example"
        attempts = []
        recording_open = self.mod._OPENER.open

        def open_timing_out(fullurl, data=None, timeout=None):
            if fullurl.full_url.startswith(f"{base_a}/"):
                attempts.append(fullurl.full_url)
                raise TimeoutError("timed out")
            return recording_open(fullurl, data=data, timeout=timeout)

        pairs = [(base_a, "key-a"), (base_b, "key-b")]
        provider = self.mod.BazarrPeerProvider()
        with patch.object(self.mod._OPENER, "open", open_timing_out):
            self.assertEqual(len(self.search(pairs, provider=provider)), 1)
            self.assertEqual(len(attempts), 1)
            self.clock.now += 59
            self.assertEqual(len(self.search(pairs, provider=provider)), 1)
            self.assertEqual(len(attempts), 1)
            self.clock.now += 1
            self.assertEqual(len(self.search(pairs, provider=provider)), 1)
            self.assertEqual(len(attempts), 2)
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.search([(base_a, "key-a")], provider=provider)
            self.assertEqual(len(attempts), 2)

    def test_cooldown_applies_only_to_the_api_key_it_was_earned_with(self):
        def respond(record):
            if record["api_key"] == "key-b":
                return _envelope([_row(4)])
            return _json({"message": "Invalid API key"}, status=403)

        server, base = self.serve(respond)
        provider = self.mod.BazarrPeerProvider()
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(self.search([(base, "key-b")], provider=provider)), 1)
        self.assertEqual([request["api_key"] for request in server.requests], ["key-a", "key-b"])
        self.assertNotIn("key-a", repr(provider._cooldowns))


class LibraryTests(PeerTestCase):
    def test_empty_library_without_the_local_provider_is_library_not_served_and_not_an_error(self):
        server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"data": [{"name": "Subs"}]}),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertEqual(self.search([(base, "key-a")]), [])
        self.assertIn("library_not_served", "\n".join(logs.output))
        self.assertIn("/api/v1/providers", [request["path"] for request in server.requests])

    def test_empty_library_with_the_local_provider_is_a_normal_success(self):
        _server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"data": [{"name": "local"}, {"name": "Subs"}]}),
        )
        self.assertEqual(self.search([(base, "key-a")]), [])

    def test_providers_answer_is_cached_for_ten_minutes(self):
        server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"data": [{"name": "Subs"}]}),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.search([(base, "key-a")], provider=provider)
            self.clock.now += 300
            self.search([(base, "key-a")], provider=provider)
        providers_calls = [request for request in server.requests if request["path"] == "/api/v1/providers"]
        self.assertEqual(len(providers_calls), 1)
        self.clock.now += 301
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.search([(base, "key-a")], provider=provider)
        providers_calls = [request for request in server.requests if request["path"] == "/api/v1/providers"]
        self.assertEqual(len(providers_calls), 2)

    def test_library_only_off_never_probes_the_providers_route(self):
        server, base = self.routes(search=lambda record: _envelope([]), providers=lambda record: _json({"data": []}))
        self.search([(base, "key-a")], library_only=False)
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/subtitles"])

    def test_serve_local_on_returns_the_library_rows_with_the_local_shape(self):
        _server, base = self.serve(lambda record: _envelope([_local_row(1234)]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertEqual(candidate["id"], f"{_tag(base)}:local-movie-1160419-en|{FILE_NAME}")
        self.assertEqual(candidate["provider_payload"]["row"], "local")

    def test_probe_carries_the_api_key_and_no_authorization_header(self):
        server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"data": [{"name": "local"}]}),
        )
        self.search([(base, "key-a")])
        (probe,) = [request for request in server.requests if request["path"] == "/api/v1/providers"]
        self.assertEqual((probe["method"], probe["api_key"], probe["auth"]), ("GET", "key-a", None))

    def test_a_transient_probe_failure_returns_the_empty_result_without_a_cooldown(self):
        server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"message": "busy"}, status=503, headers={"x-reason": "upstream"}),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertEqual(self.search([(base, "key-a")], provider=provider), [])
        text = "\n".join(logs.output)
        self.assertIn("probe_failed", text)
        self.assertIn("ServiceUnavailable", text)
        self.assertNotIn("library_not_served", text)
        self.assertEqual(provider._cooldowns, {})
        self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(self.searches(server)), 2)

    def test_a_probe_timeout_is_a_probe_failure_with_the_empty_result(self):
        _server, base = self.routes(search=lambda record: _envelope([]), providers=lambda record: _json({"data": []}))
        recording_open = self.mod._OPENER.open

        def open_timing_out_on_the_probe(fullurl, data=None, timeout=None):
            if fullurl.full_url.endswith("/api/v1/providers"):
                raise TimeoutError("timed out")
            return recording_open(fullurl, data=data, timeout=timeout)

        with patch.object(self.mod._OPENER, "open", open_timing_out_on_the_probe):
            with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                self.assertEqual(self.search([(base, "key-a")]), [])
        self.assertIn("probe_failed", "\n".join(logs.output))

    def test_a_redirect_on_the_probe_is_a_probe_failure_with_the_empty_result(self):
        target, target_base = self.serve(lambda record: _json({"data": [{"name": "local"}]}))
        _server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: (302, {"Location": f"{target_base}/api/v1/providers"}, b""),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertEqual(self.search([(base, "key-a")]), [])
        self.assertIn("probe_failed", "\n".join(logs.output))
        self.assertEqual(target.requests, [])

    def test_a_probe_401_is_an_authentication_error(self):
        _server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"message": "Token invalid or expired"}, status=401),
        )
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")])

    def test_library_probe_is_skipped_when_the_search_spent_the_budget(self):
        original_parse = self.mod._parse_search

        def parse_and_spend_the_budget(body):
            parsed = original_parse(body)
            self.clock.now += 45
            return parsed

        server, base = self.routes(search=lambda record: _envelope([]), providers=lambda record: _json({"data": []}))
        with patch.object(self.mod, "_parse_search", parse_and_spend_the_budget):
            with self.assertLogs("bazarr_peer", level="INFO") as logs:
                self.assertEqual(self.search([(base, "key-a")]), [])
        self.assertIn("probe was skipped", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/subtitles"])

    def test_library_probe_uses_the_remaining_budget_as_its_timeout(self):
        original_parse = self.mod._parse_search

        def parse_and_spend_ten_seconds(body):
            parsed = original_parse(body)
            self.clock.now += 10
            return parsed

        server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"data": [{"name": "local"}]}),
        )
        with patch.object(self.mod, "_parse_search", parse_and_spend_ten_seconds):
            self.assertEqual(self.search([(base, "key-a")]), [])
        self.assertEqual(self.timeouts, [45, 35])
        self.assertEqual(len(self.searches(server)), 1)

    def test_a_result_list_emptied_only_by_the_ai_translated_filter_does_not_probe(self):
        server, base = self.routes(
            search=lambda record: _envelope([_row(1, ai_translated=True)]),
            providers=lambda record: _json({"data": [{"name": "Subs"}]}),
        )
        self.assertEqual(self.search([(base, "key-a")]), [])
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/subtitles"])

    def test_probe_answer_is_cached_per_key_and_records_only_whether_local_is_listed(self):
        server, base = self.routes(
            search=lambda record: _envelope([]),
            providers=lambda record: _json({"data": [{"name": "local"}]}),
        )
        provider = self.mod.BazarrPeerProvider()
        self.search([(base, "key-a")], provider=provider)
        self.search([(base, "key-a")], provider=provider)
        self.search([(base, "key-b")], provider=provider)
        probes = [request for request in server.requests if request["path"] == "/api/v1/providers"]
        self.assertEqual([request["api_key"] for request in probes], ["key-a", "key-b"])
        self.assertEqual([value for _expiry, value in provider._libraries.values()], [True, True])
        self.assertNotIn("key-a", repr(provider._libraries))


class CandidateTests(PeerTestCase):
    def test_candidate_carries_the_documented_fields_and_the_flags_come_from_the_response(self):
        _server, base = self.serve(lambda record: _envelope([_row(7, hearing_impaired=True)]))
        (candidate,) = self.search([(base, "key-a")])
        for field in DOCUMENTED_FIELDS:
            self.assertIn(field, candidate)
        self.assertEqual(candidate["id"], f"{_tag(base)}:7")
        self.assertEqual(candidate["provider"], "bazarr_peer")
        self.assertTrue(candidate["hearing_impaired"])
        self.assertTrue(candidate["language"]["hi"])
        self.assertFalse(candidate["language"]["forced"])
        self.assertFalse(candidate["hearing_impaired_verifiable"])
        self.assertEqual(candidate["filename"], FILE_NAME)
        self.assertEqual(candidate["release_info"], "Dune.2021.1080p.WEBRip.x264-GRP")

    def test_forced_flag_comes_from_foreign_parts_only(self):
        _server, base = self.serve(lambda record: _envelope([_row(7, foreign_parts_only=True)]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertTrue(candidate["language"]["forced"])
        self.assertFalse(candidate["hearing_impaired"])

    def test_absent_flags_stay_unknown(self):
        absent = _row(7)
        del absent["attributes"]["hearing_impaired"]
        del absent["attributes"]["foreign_parts_only"]
        flagged = _row(8, hearing_impaired=True, foreign_parts_only=True)
        _server, base = self.serve(lambda record: _envelope([absent, flagged]))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        unknown = by_id[f"{_tag(base)}:7"]
        self.assertIsNone(unknown["hearing_impaired"])
        self.assertIsNone(unknown["language"]["hi"])
        self.assertIsNone(unknown["language"]["forced"])
        known = by_id[f"{_tag(base)}:8"]
        self.assertIs(known["hearing_impaired"], True)
        self.assertIs(known["language"]["hi"], True)
        self.assertIs(known["language"]["forced"], True)

    def test_hash_is_claimed_only_when_the_peer_matched_the_hash_and_a_release_is_named(self):
        records = [
            _row(1, moviehash_match=True),
            _row(2, moviehash_match=True, release=""),
            _row(3, moviehash_match=False),
        ]
        _server, base = self.serve(lambda record: _envelope(records))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        exact = by_id[f"{_tag(base)}:1"]
        self.assertIn("hash", exact["matches"])
        self.assertTrue(exact["hash_verifiable"])
        self.assertEqual(exact["release_info"], "Dune.2021.1080p.WEBRip.x264-GRP")
        for number in (2, 3):
            candidate = by_id[f"{_tag(base)}:{number}"]
            self.assertNotIn("hash", candidate["matches"])
            self.assertFalse(candidate["hash_verifiable"])

    def test_regional_row_maps_to_por_with_a_country_and_never_por_dash_br(self):
        regional = {"alpha3": "por", "country_alpha2": "BR", "hi": False, "forced": False}
        _server, base = self.serve(lambda record: _envelope([_row(9, language="pt-BR")]))
        (candidate,) = self.search([(base, "key-a")], languages=[regional])
        self.assertEqual(candidate["language"]["alpha3"], "por")
        self.assertEqual(candidate["language"]["alpha2"], "pt")
        self.assertEqual(candidate["language"]["country_alpha2"], "BR")
        self.assertNotIn("por-BR", json.dumps(candidate["language"]))

    def test_regional_request_keeps_the_brazilian_rows_and_drops_the_portugal_rows(self):
        regional = {"alpha3": "por", "country_alpha2": "BR", "hi": False, "forced": False}
        records = [_row(9, language="pt-BR"), _row(10, language="pt-PT"), _row(11, language="pt")]
        _server, base = self.serve(lambda record: _envelope(records))
        ids = {candidate["id"] for candidate in self.search([(base, "key-a")], languages=[regional])}
        self.assertEqual(ids, {f"{_tag(base)}:9", f"{_tag(base)}:11"})

    def test_page_link_comes_from_the_url_field_only_when_it_is_not_empty(self):
        records = [_row(1, url="https://peer-tracker.example/subtitles/1"), _row(2, url="")]
        _server, base = self.serve(lambda record: _envelope(records))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        self.assertEqual(by_id[f"{_tag(base)}:1"]["page_link"], "https://peer-tracker.example/subtitles/1")
        self.assertNotIn("page_link", by_id[f"{_tag(base)}:2"])

    def test_page_link_keeps_only_an_absolute_web_url(self):
        records = [
            _row(1, url="javascript:alert(1)"),
            _row(2, url="ftp://peer-tracker.example/subtitles/2"),
            _row(3, url="https://peer-tracker.example/subtitles/3"),
        ]
        _server, base = self.serve(lambda record: _envelope(records))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        self.assertNotIn("page_link", by_id[f"{_tag(base)}:1"])
        self.assertNotIn("page_link", by_id[f"{_tag(base)}:2"])
        self.assertEqual(by_id[f"{_tag(base)}:3"]["page_link"], "https://peer-tracker.example/subtitles/3")

    def test_ai_translated_rows_are_dropped_unless_the_setting_includes_them(self):
        records = [_row(1), _row(2, ai_translated=True)]
        _server, base = self.serve(lambda record: _envelope(records))
        default_ids = {candidate["id"] for candidate in self.search([(base, "key-a")])}
        self.assertEqual(default_ids, {f"{_tag(base)}:1"})
        included = {
            candidate["id"]: candidate
            for candidate in self.search([(base, "key-a")], include_ai_translated=True)
        }
        self.assertEqual(set(included), {f"{_tag(base)}:1", f"{_tag(base)}:2"})
        self.assertTrue(included[f"{_tag(base)}:2"]["ai_translated"])
        self.assertFalse(included[f"{_tag(base)}:1"]["ai_translated"])

    def test_episode_matches_come_from_the_series_imdb_and_the_season_and_episode(self):
        _server, base = self.serve(lambda record: _envelope([_episode_row(4)]))
        (candidate,) = self.search([(base, "key-a")], video=EPISODE_VIDEO)
        self.assertEqual(candidate["matches"], ["series_imdb_id", "season", "episode"])
        self.assertEqual(candidate["score"], 95)
        self.assertEqual(candidate["score_without_hash"], 95)

    def test_movie_matches_on_imdb_id(self):
        _server, base = self.serve(lambda record: _envelope([_row(1)]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertEqual(candidate["matches"], ["imdb_id"])
        self.assertEqual(candidate["score"], 95)

    def test_a_row_of_another_media_type_is_not_offered(self):
        movie_row = _row(2, feature_details={"feature_type": "Movie", "imdb_id": 903747})
        _server, base = self.serve(lambda record: _envelope([_episode_row(1), movie_row]))
        ids = {candidate["id"] for candidate in self.search([(base, "key-a")], video=EPISODE_VIDEO)}
        self.assertEqual(ids, {f"{_tag(base)}:1"})

    def test_remote_and_local_rows_map_to_the_candidate_shape(self):
        remote = _row(
            1001,
            url="https://peer-tracker.example/subtitles/1001",
            hearing_impaired=True,
            foreign_parts_only=True,
        )
        local = _local_row(1234, moviehash_match=True, hearing_impaired=True, foreign_parts_only=True)
        _server, base = self.serve(lambda record: _envelope([remote, local]))
        by_id = {candidate["id"]: candidate for candidate in self.search([(base, "key-a")])}
        remote_candidate = by_id[f"{_tag(base)}:1001"]
        self.assertEqual(remote_candidate["display"]["uploader"], "bazarr:uploader1")
        self.assertEqual(remote_candidate["display"]["peer"], "127.0.0.1")
        self.assertEqual(remote_candidate["release_info"], "Dune.2021.1080p.WEBRip.x264-GRP")
        self.assertEqual(remote_candidate["filename"], FILE_NAME)
        self.assertEqual(remote_candidate["page_link"], "https://peer-tracker.example/subtitles/1001")
        self.assertTrue(remote_candidate["language"]["hi"])
        self.assertTrue(remote_candidate["language"]["forced"])
        self.assertFalse(remote_candidate["hash_verifiable"])
        self.assertEqual(remote_candidate["provider_payload"]["row"], "remote")
        self.assertEqual(remote_candidate["provider_payload"]["key"], "1001")
        self.assertEqual(remote_candidate["provider_payload"]["subtitle_id"], "1001")
        self.assertEqual(remote_candidate["provider_payload"]["file_id"], 10010)
        self.assertEqual(remote_candidate["provider_payload"]["extension"], "srt")
        local_candidate = by_id[f"{_tag(base)}:local-movie-1160419-en|{FILE_NAME}"]
        self.assertEqual(local_candidate["display"]["uploader"], "bazarr:local")
        self.assertEqual(local_candidate["release_info"], FILE_NAME)
        self.assertEqual(local_candidate["filename"], FILE_NAME)
        self.assertNotIn("page_link", local_candidate)
        self.assertTrue(local_candidate["language"]["hi"])
        self.assertTrue(local_candidate["language"]["forced"])
        self.assertTrue(local_candidate["hash_verifiable"])
        self.assertEqual(local_candidate["provider_payload"]["row"], "local")
        self.assertEqual(local_candidate["provider_payload"]["key"], f"local-movie-1160419-en|{FILE_NAME}")
        self.assertEqual(local_candidate["provider_payload"]["subtitle_id"], "local-movie-1160419-en-1234")
        self.assertEqual(local_candidate["provider_payload"]["file_id"], 1234)
        self.assertEqual(local_candidate["provider_payload"]["extension"], "srt")

    def test_local_row_hash_claim_follows_its_moviehash_match(self):
        _server, base = self.serve(lambda record: _envelope([_local_row(1234, moviehash_match=True)]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertTrue(candidate["hash_verifiable"])
        self.assertIn("hash", candidate["matches"])

    def test_local_row_without_a_file_name_is_not_offered_under_any_file_id(self):
        rows = [_local_row(1234, name="")]
        server, base = self.serve(lambda record: _envelope(rows))
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="DEBUG") as logs:
            self.assertEqual(self.search([(base, "key-a")], provider=provider), [])
        output = "\n".join(logs.output)
        self.assertIn(f"endpoint {_tag(base)}: a local row without a file name is not offered (file 1234)", output)
        self.assertNotIn("local-movie", output)
        rows[:] = [_local_row(5678, name="")]
        self.assertEqual(self.search([(base, "key-a")], provider=provider), [])
        self.assertEqual(len(self.searches(server)), 2)

    def test_version_skew_missing_optional_attributes_default_safely(self):
        row = {
            "id": "77",
            "type": "subtitle",
            "attributes": {"subtitle_id": "77", "language": "en", "files": [{"file_id": 770, "file_name": "a.srt"}]},
        }
        _server, base = self.serve(lambda record: _envelope([row]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertEqual(candidate["release_info"], "")
        self.assertFalse(candidate["hash_verifiable"])
        self.assertIsNone(candidate["hearing_impaired"])
        self.assertNotIn("page_link", candidate)

    def test_worker_ids_are_namespaced_by_endpoint(self):
        _a, base_a = self.serve(lambda record: _envelope([_row(42)]))
        _b, base_b = self.serve(lambda record: _envelope([_row(42)]))
        ids = sorted(candidate["id"] for candidate in self.search([(base_a, "key-a"), (base_b, "key-b")]))
        self.assertEqual(ids, sorted([f"{_tag(base_a)}:42", f"{_tag(base_b)}:42"]))

    def test_loop_fanout_never_returns_the_consumers_own_candidates(self):
        consumer_row = _row(5, uploader={"name": "bazarr_peer:consumer"})

        def respond(record):
            if record["query"].get("exclude_providers") == "bazarr_peer":
                return _envelope([_row(6)])
            return _envelope([consumer_row])

        server, base = self.serve(respond)
        results = self.search([(base, "key-a")])
        self.assertEqual([candidate["id"] for candidate in results], [f"{_tag(base)}:6"])
        self.assertTrue(all(request["query"].get("exclude_providers") == "bazarr_peer" for request in server.requests))

    def test_payload_carries_no_secrets_and_no_nested_provider_payload_key(self):
        _server, base = self.serve(lambda record: _envelope([_row(1)]))
        (candidate,) = self.search([(base, "key-a")])
        payload = candidate["provider_payload"]
        self.assertNotIn("provider_payload", payload)
        self.assertNotIn("key-a", json.dumps(candidate))
        self.assertNotIn("tok-1", json.dumps(candidate))

    def test_oversized_payload_drops_the_context_and_the_key_and_keeps_the_identity_fields(self):
        long_row = _row(1, subtitle_id="x" * 1100)
        _server, base = self.serve(lambda record: _envelope([long_row]))
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            (candidate,) = self.search([(base, "key-a")])
        payload = candidate["provider_payload"]
        self.assertNotIn("context", payload)
        self.assertNotIn("key", payload)
        self.assertEqual(payload["endpoint"], _tag(base))
        self.assertEqual(payload["row"], "remote")
        self.assertEqual(payload["subtitle_id"], "x" * 1100)
        self.assertEqual(payload["extension"], "srt")
        self.assertEqual(payload["file_id"], 10)
        self.assertIn("context_too_large", "\n".join(logs.output))

    def test_query_over_128_characters_is_dropped_from_the_context_with_a_log_line(self):
        long_name = "breaking.bad." + "x" * 140 + ".s01e02.720p.mkv"
        server, base = self.serve(lambda record: _envelope([_episode_row(1)]))
        video = dict(EPISODE_VIDEO, series_imdb_id=None, tmdb_id=1396, original_name=long_name)
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            (candidate,) = self.search([(base, "key-a")], video=video)
        self.assertEqual(self.searches(server)[0]["query"]["query"], long_name)
        context = candidate["provider_payload"]["context"]
        self.assertEqual(context["tmdb_id"], 1396)
        self.assertNotIn("query", context)
        self.assertIn("query_dropped", "\n".join(logs.output))

    def test_duplicate_subtitle_ids_in_one_response_keep_the_first_row(self):
        first = _row(7, download_count=3)
        second = _row(7, download_count=99)
        _server, base = self.serve(lambda record: _envelope([first, second]))
        (candidate,) = self.search([(base, "key-a")])
        self.assertEqual(candidate["id"], f"{_tag(base)}:7")
        self.assertEqual(candidate["display"]["downloads"], 3)


class DownloadTests(PeerTestCase):
    def payload(self, base, **overrides):
        payload = {
            "provider": "bazarr_peer",
            "schema": 1,
            "endpoint": _tag(base),
            "row": "remote",
            "key": "1001",
            "file_id": 10,
            "context": {"type": "movie", "imdb_id": "tt1160419", "languages": ["en"]},
        }
        payload.update(overrides)
        return payload

    def download(self, base, payload=None, provider=None, library_only=True):
        return (provider or self.mod.BazarrPeerProvider()).download(
            provider_payload=payload if payload is not None else self.payload(base),
            language=ENGLISH,
            config=_config([(base, "key-a")], library_only=library_only),
        )

    def test_download_logs_in_posts_with_the_bearer_token_and_fetches_the_stream_without_auth(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        server, base = self.routes(
            login=_login_response,
            download=_download_response,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        content = self.download(base)
        login, post, stream = server.requests
        self.assertEqual(
            (login["method"], login["path"], login["api_key"], login["auth"]),
            ("POST", "/api/v1/login", "key-a", None),
        )
        self.assertEqual((post["method"], post["path"], post["api_key"]), ("POST", "/api/v1/download", "key-a"))
        self.assertEqual(post["auth"], "Bearer tok-1")
        self.assertEqual(post["content_type"], "application/json")
        self.assertEqual(json.loads(post["body"]), {"file_id": 10, "sub_format": "srt"})
        self.assertEqual((stream["method"], stream["api_key"], stream["auth"]), ("GET", None, None))
        self.assertEqual(base64.b64decode(content["content_b64"]), body)
        self.assertEqual(content["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["format"], "srt")
        self.assertFalse(content["empty"])

    def test_token_is_reminted_once_on_a_401_and_the_retry_succeeds(self):
        state = {"posts": 0}
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"

        def download(record):
            state["posts"] += 1
            if state["posts"] == 1:
                return _json({"message": "Token invalid or expired"}, status=401)
            return _download_response(record)

        server, base = self.routes(
            login=_login_response,
            download=download,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        content = self.download(base)
        self.assertFalse(content["empty"])
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/login"), 2)

    def test_second_401_after_the_remint_skips_the_candidate_and_cools_the_endpoint(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json({"message": "Token invalid or expired"}, status=401),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn("AuthenticationError", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/login"), 2)
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(self.searches(server), [])

    def test_forbidden_download_skips_the_candidate_without_a_relogin_and_cools_the_endpoint(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json({"message": "Invalid API key"}, status=403, headers={"x-reason": "auth"}),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn("AuthenticationError", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/login"), 1)
        with self.assertRaises(self.mod.AuthenticationError):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(self.searches(server), [])

    def test_download_cooldown_never_shortens_a_rate_limit_cooldown_still_in_force(self):
        server, base = self.routes(
            search=lambda record: _throttled("600"),
            login=lambda record: _json({"message": "Invalid API key"}, status=403, headers={"x-reason": "auth"}),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.clock.now += 100
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.assertTrue(self.download(base, provider=provider)["empty"])
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(caught.exception.retry_after, 500)
        self.assertEqual(len(self.searches(server)), 1)

    def test_quota_exhausted_406_is_a_candidate_skip_and_the_provider_stays_good(self):
        state = {"posts": 0}
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"

        def download(record):
            state["posts"] += 1
            if state["posts"] == 1:
                return _json({"message": "download quota exhausted"}, status=406, headers={"x-reason": "throttled"})
            return _download_response(record)

        _server, base = self.routes(
            login=_login_response,
            download=download,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertFalse(self.download(base, provider=provider)["empty"])

    def test_stale_remote_file_id_recovers_through_the_saved_context(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
        state = {"posts": []}

        def search(record):
            return _envelope([_row(1001, file_id=77)])

        def download(record):
            file_id = json.loads(record["body"])["file_id"]
            state["posts"].append(file_id)
            if file_id == 10:
                return _json({"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"})
            return _download_response(record)

        server, base = self.routes(
            search=search,
            login=_login_response,
            download=download,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        content = self.download(base)
        self.assertFalse(content["empty"])
        self.assertEqual(state["posts"], [10, 77])
        (recovery,) = [request for request in server.requests if request["path"] == "/api/v1/subtitles"]
        self.assertEqual(recovery["query"]["imdb_id"], "tt1160419")
        self.assertEqual(recovery["query"]["languages"], "en")
        self.assertEqual(recovery["query"]["exclude_providers"], "bazarr_peer")
        self.assertEqual(recovery["query"]["only_providers"], "local")
        self.assertEqual(recovery["api_key"], "key-a")
        self.assertIsNone(recovery["auth"])

    def test_stale_local_file_id_recovers_by_stem_and_file_name_after_its_trailing_id_changed(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
        state = {"posts": []}

        def download(record):
            file_id = json.loads(record["body"])["file_id"]
            state["posts"].append(file_id)
            if file_id == 1234:
                return _json({"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"})
            return _download_response(record)

        server, base = self.routes(
            search=lambda record: _envelope([_local_row(5678)]),
            login=_login_response,
            download=download,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        payload = self.payload(
            base,
            row="local",
            key=f"local-movie-1160419-en|{FILE_NAME}",
            file_id=1234,
        )
        content = self.download(base, payload=payload)
        self.assertFalse(content["empty"])
        self.assertEqual(state["posts"], [1234, 5678])

    def test_local_candidate_id_survives_a_new_file_id_and_its_download_still_resolves(self):
        rows = [_local_row(1234)]
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
        server, base = self.routes(
            search=lambda record: _envelope(rows),
            login=_login_response,
            download=_download_response,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        provider = self.mod.BazarrPeerProvider()
        (first,) = self.search([(base, "key-a")], provider=provider)
        rows[:] = [_local_row(5678)]
        (second,) = self.search([(base, "key-a")], provider=provider)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["id"], f"{_tag(base)}:local-movie-1160419-en|{FILE_NAME}")
        self.assertEqual(second["provider_payload"]["file_id"], 5678)
        content = self.download(base, payload=second["provider_payload"], provider=provider)
        self.assertFalse(content["empty"])
        (post,) = [request for request in server.requests if request["path"] == "/api/v1/download"]
        self.assertEqual(json.loads(post["body"]), {"file_id": 5678, "sub_format": "srt"})

    def test_named_local_candidate_recovers_from_its_saved_payload_after_a_file_id_change(self):
        rows = [_local_row(1234)]
        state = {"posts": []}
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"

        def download(record):
            file_id = json.loads(record["body"])["file_id"]
            state["posts"].append(file_id)
            if file_id == 1234:
                return _json({"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"})
            return _download_response(record)

        _server, base = self.routes(
            search=lambda record: _envelope(rows),
            login=_login_response,
            download=download,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, body),
        )
        provider = self.mod.BazarrPeerProvider()
        (candidate,) = self.search([(base, "key-a")], provider=provider)
        rows[:] = [_local_row(5678)]
        content = self.download(base, payload=candidate["provider_payload"], provider=provider)
        self.assertFalse(content["empty"])
        self.assertEqual(state["posts"], [1234, 5678])

    def test_recovery_without_a_matching_row_is_a_candidate_skip_with_a_logged_reason(self):
        state = {"posts": 0}

        def download(record):
            state["posts"] += 1
            return _json({"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"})

        server, base = self.routes(
            search=lambda record: _envelope([_row(9999, file_id=88)]),
            login=_login_response,
            download=download,
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("stale_not_found", "\n".join(logs.output))
        self.assertEqual(state["posts"], 1)

    def test_at_most_one_recovery_runs_per_download_call(self):
        state = {"posts": 0}

        def download(record):
            state["posts"] += 1
            return _json({"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"})

        server, base = self.routes(
            search=lambda record: _envelope([_row(1001, file_id=77)]),
            login=_login_response,
            download=download,
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn(": not_found", "\n".join(logs.output))
        self.assertEqual(state["posts"], 2)
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 1)

    def test_a_download_without_saved_context_is_not_recovered(self):
        server, base = self.routes(
            search=lambda record: _envelope([_row(1001, file_id=77)]),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        payload = self.payload(base)
        del payload["context"]
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.assertTrue(self.download(base, payload=payload)["empty"])
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 0)

    def test_rate_limit_on_the_recovery_search_cools_the_endpoint_and_skips_with_recovery_throttled(self):
        server, base = self.routes(
            search=lambda record: _throttled("120"),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn(": recovery_throttled", "\n".join(logs.output))
        with self.assertRaises(self.mod.APIThrottled):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 1)

    def test_forbidden_recovery_search_skips_with_recovery_rejected_and_no_cooldown_or_relogin(self):
        server, base = self.routes(
            search=lambda record: _json({"message": "Invalid API key"}, status=403, headers={"x-reason": "auth"}),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn(": recovery_rejected", "\n".join(logs.output))
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/login"), 1)

    def test_recovery_server_error_skips_without_cooldown(self):
        state = {"searches": 0}

        def search(record):
            state["searches"] += 1
            if state["searches"] == 1:
                return _json({"message": "down"}, status=500)
            return _envelope([_row(1001, file_id=77)])

        server, base = self.routes(
            search=search,
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn(": recovery_failed", "\n".join(logs.output))
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual(len(self.searches(server)), 1)
        self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(self.searches(server)), 2)

    def test_recovery_unreachable_skips_without_cooldown(self):
        server, base = self.routes(
            search=lambda record: _envelope([_row(1001, file_id=77)]),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        opener = self.mod._OPENER.open
        state = {"refused": False}

        def refuse_the_recovery_search(fullurl, data=None, timeout=None):
            # The routes share one base URL, so the opener refuses only the first search, the recovery.
            if fullurl.full_url.startswith(f"{base}/api/v1/subtitles") and not state["refused"]:
                state["refused"] = True
                raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
            return opener(fullurl, data=data, timeout=timeout)

        provider = self.mod.BazarrPeerProvider()
        with patch.object(self.mod._OPENER, "open", refuse_the_recovery_search):
            with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn(": recovery_failed", "\n".join(logs.output))
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual(self.searches(server), [])
        self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(self.searches(server)), 1)

    def test_tls_failure_on_the_recovery_search_skips_the_candidate_and_cools_the_endpoint(self):
        server, base = self.routes(
            search=lambda record: _envelope([_row(1001, file_id=77)]),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        recording_open = self.mod._OPENER.open
        state = {"searches": 0}

        def open_second_search_failing_handshake(fullurl, data=None, timeout=None):
            if fullurl.full_url.startswith(f"{base}/api/v1/subtitles"):
                state["searches"] += 1
                if state["searches"] == 2:
                    raise urllib.error.URLError(ssl.SSLError("the TLS handshake failed"))
            return recording_open(fullurl, data=data, timeout=timeout)

        provider = self.mod.BazarrPeerProvider()
        with patch.object(self.mod._OPENER, "open", open_second_search_failing_handshake):
            (candidate,) = self.search([(base, "key-a")], provider=provider)
            with self.assertLogs("bazarr_peer", level="WARNING"):
                content = self.download(base, payload=candidate["provider_payload"], provider=provider)
        self.assertTrue(content["empty"])
        self.assertEqual(state["searches"], 2)
        ((endpoint_key, (cooled_until, error)),) = provider._cooldowns.items()
        self.assertEqual(endpoint_key[0], _tag(base))
        self.assertIsInstance(error, self.mod.ConfigurationError)
        self.assertEqual(cooled_until - self.clock.now, 60)

    def test_recovery_refusal_skips_without_cooldown(self):
        state = {"searches": 0}

        def search(record):
            state["searches"] += 1
            if state["searches"] == 1:
                return _json({"message": "not acceptable"}, status=406)
            return _envelope([_row(1001, file_id=77)])

        server, base = self.routes(
            search=search,
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn(": recovery_failed", "\n".join(logs.output))
        self.assertEqual(provider._cooldowns, {})
        self.assertEqual(len(self.searches(server)), 1)
        self.search([(base, "key-a")], provider=provider)
        self.assertEqual(len(self.searches(server)), 2)

    def test_bad_request_on_the_recovery_search_skips_with_bad_request(self):
        _server, base = self.routes(
            search=lambda record: _json(
                {"message": "invalid language code"}, status=400, headers={"x-reason": "bad-request"}
            ),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn(": bad_request", "\n".join(logs.output))

    def test_compat_disabled_on_the_recovery_search_skips_with_compat_disabled(self):
        _server, base = self.routes(
            search=lambda record: _json({"message": "disabled"}, status=404, headers={"x-reason": "compat-disabled"}),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn(": compat_disabled", "\n".join(logs.output))

    def test_recovery_whose_budget_is_spent_before_its_search_sends_nothing(self):
        def download(record):
            # The download call has 30 seconds. The peer answers 404 after they have passed, so no recovery starts.
            self.clock.now += 31
            return _json({"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"})

        server, base = self.routes(
            search=lambda record: _envelope([_row(1001, file_id=77)]),
            login=_login_response,
            download=download,
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn(": budget_spent", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 0)

    def test_compat_disabled_on_the_download_skips_without_a_recovery_and_cools_the_endpoint(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json({"message": "disabled"}, status=404, headers={"x-reason": "compat-disabled"}),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn(DISTRIBUTION_OFF, "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 0)
        with self.assertRaisesRegex(self.mod.ConfigurationError, DISTRIBUTION_OFF):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(self.searches(server), [])

    def test_a_dropped_context_skips_a_stale_download_with_stale_no_context(self):
        long_row = _row(1001, file_id=77, subtitle_id="x" * 1100)
        server, base = self.routes(
            search=lambda record: _envelope([long_row]),
            login=_login_response,
            download=lambda record: _json(
                {"message": "subtitle not found"}, status=404, headers={"x-reason": "not_found"}
            ),
        )
        provider = self.mod.BazarrPeerProvider()
        (candidate,) = self.search([(base, "key-a")], provider=provider)
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            content = self.download(base, payload=candidate["provider_payload"], provider=provider)
        self.assertTrue(content["empty"])
        self.assertIn(": stale_no_context", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 1)

    def test_redirect_on_the_login_is_a_candidate_skip_and_the_target_gets_no_request(self):
        target, target_base = self.serve(lambda record: _login_response(record))
        server, base = self.routes(
            login=lambda record: (302, {"Location": f"{target_base}/api/v1/login"}, b""),
            download=_download_response,
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("redirects are refused", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/login"])
        self.assertEqual(target.requests, [])

    def test_redirect_on_the_download_is_a_candidate_skip_and_the_target_gets_no_request(self):
        target, target_base = self.serve(lambda record: _download_response(record))
        server, base = self.routes(
            login=_login_response,
            download=lambda record: (302, {"Location": f"{target_base}/api/v1/download"}, b""),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("redirects are refused", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/login", "/api/v1/download"])
        self.assertEqual(target.requests, [])

    def test_stream_statuses_are_candidate_skips_with_a_reason_each(self):
        # Each status comes with the x-reason and the body the peer sends for it. Each one skips the
        # candidate, logs its reason label, and keeps the peer's message out of the log.
        cases = (
            (410, "not_found", "stream token expired", "stream_expired"),
            (404, "not_found", "stream not found", "not_found"),
            (403, "auth", "provider URL blocked by SSRF guard", "blocked_by_peer"),
            (503, "upstream", "provider fetch failed", "peer_unavailable"),
        )
        for status, reason, message, label in cases:
            with self.subTest(status=status):
                _server, base = self.routes(
                    login=_login_response,
                    download=_download_response,
                    stream=lambda record, status=status, reason=reason, message=message: _json(
                        {"message": message}, status=status, headers={"x-reason": reason}
                    ),
                )
                with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                    self.assertTrue(self.download(base)["empty"])
                text = "\n".join(logs.output)
                self.assertIn(f": {label}", text)
                self.assertNotIn(message, text)

    def test_empty_stream_body_is_a_candidate_skip_without_a_retry(self):
        server, base = self.routes(
            login=_login_response,
            download=_download_response,
            stream=lambda record: (200, {"Content-Type": "application/x-subrip"}, b""),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("content_missing", "\n".join(logs.output))
        self.assertEqual(len([request for request in server.requests if request["method"] == "GET"]), 1)

    def test_link_from_another_origin_is_refused_without_a_stream_request(self):
        target, target_base = self.serve(lambda record: (200, {"Content-Type": "application/x-subrip"}, b"x"))
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json({"link": f"{target_base}/api/v1/download/stream/tok-1"}),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("link_origin_mismatch", "\n".join(logs.output))
        self.assertEqual(target.requests, [])

    def test_link_with_another_base_path_is_refused(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json({"link": f"http://{record['host']}/other/api/v1/download/stream/tok-1"}),
            prefix="/peer",
        )
        base_with_path = f"{base}/peer"
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base_with_path)["empty"])
        self.assertIn("link_origin_mismatch", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests if request["method"] == "GET"], [])

    def test_redirect_on_the_stream_is_refused_and_not_followed(self):
        target, target_base = self.serve(lambda record: (200, {"Content-Type": "application/x-subrip"}, b"x"))
        server, base = self.routes(
            login=_login_response,
            download=_download_response,
            stream=lambda record: (302, {"Location": f"{target_base}/hit"}, b""),
        )
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.assertTrue(self.download(base)["empty"])
        self.assertEqual(target.requests, [])

    def test_tls_handshake_failure_on_the_stream_skips_the_candidate_and_cools_the_endpoint(self):
        server, base = self.routes(login=_login_response, download=_download_response)
        recording_open = self.mod._OPENER.open

        def open_failing_handshake(fullurl, data=None, timeout=None):
            if fullurl.full_url.startswith(f"{base}/api/v1/download/stream/"):
                raise urllib.error.URLError(ssl.SSLError("the TLS handshake failed"))
            return recording_open(fullurl, data=data, timeout=timeout)

        provider = self.mod.BazarrPeerProvider()
        with patch.object(self.mod._OPENER, "open", open_failing_handshake):
            with self.assertLogs("bazarr_peer", level="WARNING") as logs:
                self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn("ConfigurationError: the TLS handshake with the endpoint failed", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/login", "/api/v1/download"])
        with self.assertRaises(self.mod.ConfigurationError):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(self.searches(server), [])

    def test_slow_drip_stream_is_cut_at_the_deadline(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowDripHandler)
        server.started = threading.Event()
        server.release = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.release.set)
        link = f"http://127.0.0.1:{server.server_address[1]}/api/v1/download/stream/tok-1"
        with patch.object(self.mod, "_monotonic", time.monotonic):
            started = time.monotonic()
            with self.assertRaises(self.mod.ServiceUnavailable):
                self.mod._fetch_stream(link, 10, started + 1.0)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertTrue(server.started.wait(5))

    def test_pack_archive_is_returned_unchanged_with_its_episode(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Show.S01E02.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        archive_bytes = buffer.getvalue()
        _server, base = self.routes(
            login=_login_response,
            download=_download_response,
            stream=lambda record: (200, {"Content-Type": "application/zip"}, archive_bytes),
        )
        payload = self.payload(base, episode=2)
        content = self.download(base, payload=payload)
        self.assertEqual(base64.b64decode(content["archive_b64"]), archive_bytes)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(archive_bytes).hexdigest())
        self.assertEqual(content["episode"], 2)
        self.assertNotIn("content_b64", content)

    def test_rar_json_and_html_downloads_are_candidate_skips(self):
        rar = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 64
        cases = (
            (b"", "content"),
            (rar, "RAR"),
            (b'{"message": "refused"}', "JSON"),
            (b"<!DOCTYPE html><html></html>", "web page"),
        )
        for body, label in cases:
            with self.subTest(label=label):
                _server, base = self.routes(
                    login=_login_response,
                    download=_download_response,
                    stream=lambda record, body=body: (200, {"Content-Type": "application/octet-stream"}, body),
                )
                with self.assertLogs("bazarr_peer", level="WARNING"):
                    self.assertTrue(self.download(base)["empty"])

    def test_bad_request_on_the_download_skips_the_candidate_and_cools_the_endpoint(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json({"message": "bad"}, status=400, headers={"x-reason": "bad-request"}),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn("bad_request", "\n".join(logs.output))
        with self.assertRaisesRegex(self.mod.ConfigurationError, "bad_request"):
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(self.searches(server), [])

    def test_rate_limited_download_skips_the_candidate_and_cools_the_endpoint_for_its_retry_after(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _throttled("120"),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn("APIThrottled", "\n".join(logs.output))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.search([(base, "key-a")], provider=provider)
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertEqual([request["path"] for request in server.requests].count("/api/v1/subtitles"), 0)

    def test_download_for_an_endpoint_that_is_not_configured_is_a_candidate_skip_with_no_request(self):
        server, base = self.serve(lambda record: (200, {}, b"x"))
        payload = self.payload(base, endpoint="0" * 16)
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            content = self.download(base, payload=payload)
        self.assertTrue(content["empty"])
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(server.requests, [])

    def test_transient_refusal_on_the_download_request_is_a_candidate_skip(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _json(
                {"message": "provider fetch failed"}, status=503, headers={"x-reason": "upstream"}
            ),
        )
        provider = self.mod.BazarrPeerProvider()
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base, provider=provider)["empty"])
        self.assertIn("peer_unavailable", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/login", "/api/v1/download"])
        self.assertEqual(provider._cooldowns, {})

    def test_unreachable_download_is_a_candidate_skip(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with self.assertLogs("bazarr_peer", level="WARNING"):
            self.assertTrue(self.download(base)["empty"])

    def test_cut_chunked_error_body_on_login_is_a_candidate_skip(self):
        server, base = self.routes(
            login=lambda record: _raw_error(500, b'24\r\n{"error": "cut sh'),
            download=_download_response,
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("the peer failed to answer (HTTP 500)", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/login"])

    def test_cut_chunked_error_body_on_download_is_a_candidate_skip(self):
        server, base = self.routes(
            login=_login_response,
            download=lambda record: _raw_error(500, b'24\r\n{"error": "cut sh'),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("the peer failed to answer (HTTP 500)", "\n".join(logs.output))
        self.assertEqual([request["path"] for request in server.requests], ["/api/v1/login", "/api/v1/download"])

    def test_cut_chunked_error_body_on_the_stream_is_a_candidate_skip(self):
        server, base = self.routes(
            login=_login_response,
            download=_download_response,
            stream=lambda record: _raw_error(500, b'24\r\n{"error": "cut sh'),
        )
        with self.assertLogs("bazarr_peer", level="WARNING") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertIn("file 10: HTTP 500", "\n".join(logs.output))
        self.assertEqual([request["method"] for request in server.requests], ["POST", "POST", "GET"])

    def test_download_logs_never_carry_the_token_the_link_or_the_body(self):
        def leak(name):
            return {"error": f"SECRET-{name}-ERROR", "message": f"SECRET-{name}-MESSAGE"}

        cases = (
            ("LOGIN", {"login": lambda record: _json(leak("LOGIN"), status=500)}),
            (
                "DOWNLOAD",
                {
                    "login": _login_response,
                    "download": lambda record: _json(leak("DOWNLOAD"), status=503, headers={"x-reason": "upstream"}),
                },
            ),
            (
                "STREAM",
                {
                    "login": _login_response,
                    "download": _download_response,
                    "stream": lambda record: _json(leak("STREAM"), status=410, headers={"x-reason": "not_found"}),
                },
            ),
        )
        for name, routes in cases:
            with self.subTest(route=name):
                _server, base = self.routes(**routes)
                with self.assertLogs("bazarr_peer", level="DEBUG") as logs:
                    self.assertTrue(self.download(base)["empty"])
                text = "\n".join(logs.output)
                self.assertNotIn(f"SECRET-{name}", text)
                self.assertNotIn("tok-1", text)
                self.assertNotIn("/api/v1/download/stream/", text)
                self.assertNotIn("key-a", text)
        _server, base = self.routes(
            login=_login_response,
            download=lambda record: _json(leak("BAD"), status=400, headers={"x-reason": "bad-request"}),
        )
        with self.assertLogs("bazarr_peer", level="DEBUG") as logs:
            self.assertTrue(self.download(base)["empty"])
        self.assertNotIn("SECRET-BAD", "\n".join(logs.output))


class HashTests(PeerTestCase):
    def test_opensubtitles_hash_matches_the_reference_algorithm(self):
        data = bytes((index * 7) % 256 for index in range(300 * 1024))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "clip.mkv")
            with open(path, "wb") as handle:
                handle.write(data)
            self.assertEqual(self.mod._opensubtitles_hash(path), _reference_hash(data))

    def test_opensubtitles_hash_needs_at_least_128_kib_and_a_readable_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "short.mkv")
            with open(path, "wb") as handle:
                handle.write(b"\x01" * (128 * 1024 - 1))
            self.assertIsNone(self.mod._opensubtitles_hash(path))
            self.assertIsNone(self.mod._opensubtitles_hash(os.path.join(directory, "missing.mkv")))
            self.assertIsNone(self.mod._opensubtitles_hash(None))


class ManifestTests(PeerTestCase):
    def test_manifest_declares_the_settings_the_secret_and_the_languages(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        schema = manifest["config_schema"]
        properties = schema["properties"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(properties),
            {"endpoints", "api_keys", "library_only", "use_hash", "include_ai_translated", "timeout_seconds"},
        )
        self.assertEqual(manifest["secret_fields"], ["api_keys"])
        self.assertTrue(properties["api_keys"]["secret"])
        self.assertNotIn("secret", properties["endpoints"])
        self.assertEqual(properties["library_only"]["type"], "boolean")
        self.assertIs(properties["library_only"]["default"], True)
        self.assertIs(properties["use_hash"]["default"], True)
        self.assertIs(properties["include_ai_translated"]["default"], False)
        timeout = properties["timeout_seconds"]
        self.assertEqual(
            (timeout["type"], timeout["default"], timeout["minimum"], timeout["maximum"]),
            ("integer", 45, 30, 90),
        )
        self.assertEqual(manifest["provider_id"], "bazarr_peer")
        self.assertEqual(manifest["name"], "Bazarr+ Peer")
        self.assertEqual(manifest["entry_class"], "BazarrPeerProvider")
        self.assertTrue(hasattr(self.mod, manifest["entry_class"]))
        self.assertEqual(manifest["languages"], sorted([*self.mod._LANGUAGE_CODES, "por-BR"]))
        self.assertEqual(manifest["supported_media"], ["movie", "episode"])
        self.assertEqual(manifest["source"]["path"], "providers/bazarr_peer")

    def test_endpoints_setting_declares_the_uri_format(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["config_schema"]["properties"]["endpoints"]["format"], "uri")


if __name__ == "__main__":
    unittest.main()
