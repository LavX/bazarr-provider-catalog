import base64
import email.message
import hashlib
import http.client
import http.server
import importlib.util
import io
import socket
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import urllib.response
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "greeksubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "greeksubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DUNE_HTML = (FIXTURE_DIR / "greeksubtitles_search_dune.html").read_bytes()
GOT_PAGE0_HTML = (FIXTURE_DIR / "greeksubtitles_search_game_of_thrones_page0.html").read_bytes()
GOT_PAGE1_HTML = (FIXTURE_DIR / "greeksubtitles_search_game_of_thrones_page1.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class GreekSubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_page_extracts_language_rows_and_download_ids(self):
        page = self.mod.parse_search_page(DUNE_HTML, "https://gr.greek-subtitles.com/search.php?name=Dune+2021")

        self.assertIsNone(page["next_url"])
        self.assertEqual(len(page["rows"]), 2)
        self.assertEqual(page["rows"][0]["subtitle_id"], "2793668")
        self.assertEqual(page["rows"][0]["language"], "ell")
        self.assertEqual(page["rows"][0]["alpha2"], "el")
        self.assertEqual(page["rows"][0]["page_url"], "http://subtitles.gr/subtitles/Dune-2021-1080p-WEBRip-x264-AAC5-1-YTS-MX-/2793668/")
        self.assertEqual(page["rows"][0]["release"], "Dune 2021 1080p WEBRip x264 AAC5 1 YTS MX")
        self.assertEqual(page["rows"][0]["downloads"], 321)
        self.assertEqual(page["rows"][1]["language"], "eng")
        self.assertEqual(page["rows"][1]["alpha2"], "en")

    def test_parse_search_page_extracts_next_page_url(self):
        page = self.mod.parse_search_page(
            GOT_PAGE0_HTML,
            "https://gr.greek-subtitles.com/search.php?name=Game+of+Thrones+S01E01",
        )

        self.assertEqual(
            page["next_url"],
            "https://gr.greek-subtitles.com/search.php?page=1&name=Game%20of%20Thrones%20S01E01&sort=name",
        )
        self.assertEqual(page["rows"], [])

    def test_build_search_queries_matches_movie_and_episode_flow(self):
        self.assertEqual(
            self.mod.build_search_queries(
                {
                    "kind": "episode",
                    "series": "Game of Thrones",
                    "alternative_series": ["GoT"],
                    "season": 1,
                    "episode": 1,
                }
            ),
            ["Game of Thrones S01E01", "GoT S01E01"],
        )
        self.assertEqual(
            self.mod.build_search_queries(
                {"kind": "movie", "title": "Dune", "alternative_titles": ["Dune: Part One"], "year": 2021}
            ),
            ["Dune 2021", "Dune: Part One 2021"],
        )

    def test_derive_matches_requires_episode_year_in_release(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": 2, "year": 2005},
            "The Office S01E02 HDTV x264",
        )

        self.assertIn("series", matches)
        self.assertIn("episode", matches)
        self.assertNotIn("year", matches)

    def test_derive_matches_compares_whole_tokens(self):
        matches = self.mod.derive_matches(
            {"kind": "movie", "title": "Ann", "year": 2021},
            "Joanne 2021 WEBRip",
        )

        self.assertNotIn("title", matches)


class GreekSubtitlesSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_returns_requested_greek_and_english_results(self):
        provider = self.mod.GreekSubtitlesProvider()
        called = []

        def stub(url, timeout=30, referer=None, deadline=None):
            del timeout, referer, deadline
            called.append(url)
            self.assertEqual(url, "https://gr.greek-subtitles.com/search.php?name=Dune+2021")
            return DUNE_HTML

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "source": "WEBRip",
                "release_group": "YTS",
            },
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, ["https://gr.greek-subtitles.com/search.php?name=Dune+2021"])
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        greek = next(item for item in results if item["language"]["alpha3"] == "ell")
        self.assertEqual(greek["provider"], "greeksubtitles")
        self.assertEqual(greek["provider_payload"]["subtitle_id"], "2793668")
        self.assertEqual(greek["provider_payload"]["download_url"], "https://www.greeksubtitles.info/getp.php?id=2793668")
        # A movie has no episode/season for host-side member selection.
        self.assertIsNone(greek["provider_payload"]["episode"])
        self.assertIsNone(greek["provider_payload"]["season"])
        self.assertIn("title", greek["matches"])
        self.assertIn("year", greek["matches"])
        self.assertIn("source", greek["matches"])
        self.assertIn("release_group", greek["matches"])

    def test_search_episode_follows_next_page(self):
        provider = self.mod.GreekSubtitlesProvider()
        responses = {
            "https://gr.greek-subtitles.com/search.php?name=Game+of+Thrones+S01E01": GOT_PAGE0_HTML,
            "https://gr.greek-subtitles.com/search.php?page=1&name=Game%20of%20Thrones%20S01E01&sort=name": GOT_PAGE1_HTML,
        }
        called = []

        def stub(url, timeout=30, referer=None, deadline=None):
            del timeout, referer, deadline
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "source": "HDTV",
                "video_codec": "x264",
                "release_group": "CTU",
            },
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, list(responses))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1659162")
        # The host needs episode (and season) to pick the archive member.
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_rejects_unsupported_language_or_media(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not run"))

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune", "year": 2021}, [{"alpha3": "fra", "alpha2": "fr"}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "series", "series": "Game of Thrones"}, [{"alpha3": "ell", "alpha2": "el"}], {}),
            [],
        )


class GreekSubtitlesDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.GreekSubtitlesProvider()
        body = _zip_body(
            {
                ".hidden.srt": "hidden",
                "info.txt": "not a subtitle",
                "subs/Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "1\r\n00:00:01,000 --> 00:00:02,000\r\nLine\r\n",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None, deadline=None: body

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1659162",
                "page_url": "http://subtitles.gr/subtitles/x/1659162/",
                "filename": "greeksubtitles.got.el.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 1)
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", content)
        self.assertNotIn("member", content)
        self.assertNotIn("encoding", content)

    def test_download_rar_archive_returns_raw_archive_for_host(self):
        provider = self.mod.GreekSubtitlesProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None, deadline=None: body

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1659162",
                "filename": "greeksubtitles.got.el.zip",
                "season": 1,
                "episode": 7,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 7)
        self.assertNotIn("content_b64", content)
        self.assertNotIn("encoding", content)

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.GreekSubtitlesProvider()
        body = _zip_body({"Dune.2021.1080p.WEBRip.srt": "movie subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None, deadline=None: body

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=2793668",
                "filename": "greeksubtitles.dune.el.zip",
                "season": None,
                "episode": None,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertIsNone(content["episode"])

    def test_download_direct_subtitle_returns_content_payload(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None, deadline=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nRaw\r\n"

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                "filename": "raw.srt",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        body = base64.b64decode(content["content_b64"])
        self.assertEqual(body, b"1\n00:00:01,000 --> 00:00:02,000\nRaw\n")
        self.assertEqual(content["format"], "srt")
        self.assertEqual(content["content_sha256"], hashlib.sha256(body).hexdigest())
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", content)
        self.assertNotIn("archive_b64", content)

    def test_download_rejects_html_error_page(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None, deadline=None: (
            b"<!doctype html><html><body>not a subtitle</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "greeksubtitles",
                    "schema": 1,
                    "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                    "filename": "greeksubtitles.failure.zip",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

    def test_download_rejects_empty_body(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None, deadline=None: b"   \r\n  "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "greeksubtitles",
                    "schema": 1,
                    "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                    "filename": "greeksubtitles.failure.zip",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

    def test_download_requires_download_url(self):
        provider = self.mod.GreekSubtitlesProvider()
        with self.assertRaises(ValueError):
            provider.download({"provider": "greeksubtitles", "schema": 1}, {"alpha3": "ell"}, {})


class _FakeClock:
    """A monotonic clock the tests move by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class _TimedOpener:
    """Stands in for the cookie opener, taking a set time per URL on the fake clock.

    A page slower than the request's timeout spends the whole timeout and then
    raises, the way a socket read does. A URL mapped to an exception raises it.
    """

    def __init__(self, clock, pages, latency=None, default_latency=1.0):
        self.clock = clock
        self.pages = pages
        self.latency = latency or {}
        self.default_latency = default_latency
        self.calls = []

    def open(self, request, timeout):
        url = request.full_url
        self.calls.append({"url": url, "timeout": timeout, "started": self.clock()})
        seconds = self.latency.get(url, self.default_latency)
        if seconds > timeout:
            self.clock.advance(timeout)
            raise socket.timeout("timed out")
        self.clock.advance(seconds)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return _FakeResponse(page)

    def urls(self):
        return [call["url"] for call in self.calls]


class _TimedHost(urllib.request.BaseHandler):
    """Answers https requests on the fake clock, behind urllib's own redirects.

    Added to the provider's real opener, ahead of its HTTPS handler. A route is
    (seconds, status, value): value is a redirect's Location or a 200's body,
    or a _Drip for a 200 whose body arrives in timed pieces. A redirect whose
    own body trickles in takes (Location, _Drip).
    A route slower than the request's timeout spends the whole timeout and then
    raises, the way a socket read does.
    """

    handler_order = 100

    def __init__(self, clock, routes):
        self.clock = clock
        self.routes = routes
        self.calls = []

    def https_open(self, request):
        url = request.full_url
        self.calls.append({"url": url, "timeout": request.timeout, "started": self.clock()})
        seconds, status, value = self.routes[url]
        if seconds > request.timeout:
            self.clock.advance(request.timeout)
            raise socket.timeout("timed out")
        self.clock.advance(seconds)
        headers = email.message.Message()
        if isinstance(value, _Drip):
            body = _DrippingBody(self.clock, value, request.timeout)
        elif status != 200 and isinstance(value, tuple):
            # A redirect whose own body trickles in: (Location, _Drip).
            headers["Location"] = value[0]
            body = _DrippingBody(self.clock, value[1], request.timeout)
        elif status != 200:
            headers["Location"] = value
            body = io.BytesIO(b"")
        else:
            body = io.BytesIO(value)
        response = urllib.response.addinfourl(body, headers, url, status)
        response.msg = "OK" if status == 200 else "Found"
        return response

    def urls(self):
        return [call["url"] for call in self.calls]



class _Drip:
    """A 200 body sent in `pieces` parts, each arriving `seconds` after the last."""

    def __init__(self, body, pieces, seconds):
        size = max(1, -(-len(body) // pieces))
        self.pieces = [body[start:start + size] for start in range(0, len(body), size)]
        self.seconds = seconds


class _FakeSocket:
    """The socket behind a response, keeping the read timeout set on it."""

    def __init__(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value


class _DrippingBody:
    """A response body that arrives one piece at a time on the fake clock.

    Each piece comes inside the socket's read timeout, so no single read times
    out however long the whole body takes, the way a server trickling bytes
    behaves. A piece slower than the socket's current timeout spends that
    timeout and raises, as a real socket read does. The socket sits where
    http.client keeps it, behind the response's file.
    """

    closed = False

    def __init__(self, clock, drip, timeout):
        self.clock = clock
        self.pieces = list(drip.pieces)
        self.seconds = drip.seconds
        self.raw = types.SimpleNamespace(_sock=_FakeSocket(timeout))

    def read1(self, size=-1):
        if not self.pieces:
            return b""
        sock = self.raw._sock
        if sock.timeout is not None and self.seconds > sock.timeout:
            self.clock.advance(sock.timeout)
            raise socket.timeout("timed out")
        self.clock.advance(self.seconds)
        return self.pieces.pop(0)

    def read(self, size=-1):
        body = b""
        piece = self.read1()
        while piece:
            body += piece
            piece = self.read1()
        return body

    def close(self):
        self.closed = True

class _FakeConnectedSocket:
    """A connected socket on the fake clock, keeping the timeout set on it.

    Each response read from it, a proxy's CONNECT reply first when there is
    one, waits the next of `waits` seconds for its first bytes. A wait longer
    than the socket's timeout spends the timeout and raises, as a real socket
    read does.
    """

    def __init__(self, clock, timeout, waits):
        self.clock = clock
        self.timeout = timeout
        self.waits = list(waits)
        self.read_timeouts = []

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return self.timeout

    def setsockopt(self, *args):
        pass

    def sendall(self, data):
        pass

    def makefile(self, mode):
        return _FirstByteWait(self, self.waits.pop(0), b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    def close(self):
        pass



class _FirstByteWait(io.BytesIO):
    """A response file whose first line waits on its socket's timeout."""

    def __init__(self, sock, seconds, body):
        super().__init__(body)
        self.sock = sock
        self.seconds = seconds
        self.waited = False

    def readline(self, size=-1):
        if not self.waited:
            self.waited = True
            sock = self.sock
            sock.read_timeouts.append(sock.timeout)
            if sock.timeout is not None and self.seconds > sock.timeout:
                sock.clock.advance(sock.timeout)
                raise socket.timeout("timed out")
            sock.clock.advance(self.seconds)
        return super().readline(size)

class _FakeTLSContext:
    """Stands in for ssl.SSLContext: the handshake takes `seconds` on the fake clock."""

    def __init__(self, clock, seconds):
        self.clock = clock
        self.seconds = seconds
        self.handshake_timeouts = []

    def wrap_socket(self, sock, server_hostname=None):
        timeout = sock.gettimeout()
        self.handshake_timeouts.append(timeout)
        if timeout is not None and self.seconds > timeout:
            self.clock.advance(timeout)
            raise socket.timeout("_ssl.c: The handshake operation timed out")
        self.clock.advance(self.seconds)
        return sock


class _StallingServer:
    """A local HTTP server: /fast answers at once, /slow holds the request."""

    def __init__(self):
        release = self.release = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/slow":
                    release.wait(5)
                body = b"<html>ok</html>"
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The client gave up at its deadline.

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

def _result_page(subtitle_id, next_page=None):
    row = (
        '<tr><td class="latest_name">1</td><td class="latest_name">'
        '<img src="http://www.subtitles.gr/flags/el.gif"/>'
        f'<a href="http://subtitles.gr/subtitles/Slow-Show-S01E01-/{subtitle_id}/">Slow Show S01E01</a>'
        '</td><td class="latest_downloads">10</td></tr>'
    )
    nav = ""
    if next_page is not None:
        nav = f'<a href = "search.php?page={next_page}&name=Slow Show S01E01&sort=name"> Next >> </a>'
    return f"<html><body><table>{row}</table>{nav}</body></html>".encode()


class GreekSubtitlesConnectionDeadlineTests(unittest.TestCase):
    """Connecting, the TLS handshake and the wait for the response each get
    only what is left before the deadline, not a fresh full timeout."""

    HOST = "gr.greek-subtitles.com"

    def setUp(self):
        self.mod = _load_provider_module()
        self.clock = _FakeClock()
        self.started = self.clock()
        self.deadline = self.started + 15.0

    def _connection(self, connect_seconds, handshake_seconds, response_seconds, tunnel_seconds=None):
        cls = self.mod._deadline_connection(http.client.HTTPSConnection, self.deadline, self.clock)
        self.tls = _FakeTLSContext(self.clock, handshake_seconds)
        connection = cls(self.HOST, timeout=15.0, context=self.tls)
        self.connect_timeouts = []
        self.sockets = []

        def fake_create_connection(address, timeout=None, source_address=None):
            self.connect_timeouts.append(timeout)
            if connect_seconds > timeout:
                self.clock.advance(timeout)
                raise socket.timeout("timed out")
            self.clock.advance(connect_seconds)
            waits = [response_seconds] if tunnel_seconds is None else [tunnel_seconds, response_seconds]
            sock = _FakeConnectedSocket(self.clock, timeout, waits)
            self.sockets.append(sock)
            return sock

        connection._create_connection = fake_create_connection
        if tunnel_seconds is not None:
            connection.set_tunnel(self.HOST)
        return connection

    def _get(self, connection):
        connection.request("GET", "/search")
        return connection.getresponse().read()

    def test_each_step_gets_only_the_time_left(self):
        connection = self._connection(2.0, 5.0, 6.0)

        self.assertEqual(self._get(connection), b"ok")

        self.assertEqual(self.connect_timeouts, [15.0])
        self.assertEqual(self.tls.handshake_timeouts, [13.0])
        self.assertEqual(self.sockets[0].read_timeouts, [8.0])

    def test_proxy_tunnel_time_is_taken_from_the_handshake(self):
        # Through an HTTP proxy the CONNECT tunnel runs between connecting
        # and the TLS handshake, on the same socket.
        connection = self._connection(1.0, 3.0, 1.0, tunnel_seconds=6.0)

        self.assertEqual(self._get(connection), b"ok")

        self.assertEqual(self.sockets[0].read_timeouts, [14.0, 5.0])
        self.assertEqual(self.tls.handshake_timeouts, [8.0])

    def test_slow_handshake_then_slow_response_ends_by_the_deadline(self):
        # Measured on the GreekSubtitles origin: a TLS handshake of five to
        # six seconds, before the page itself. Each wait fits a 15 second
        # timeout, but together they ran past the deadline.
        connection = self._connection(1.0, 6.0, 10.0)

        with self.assertRaises(TimeoutError):
            self._get(connection)

        self.assertLessEqual(self.clock() - self.started, 15.0)

    def test_no_time_left_after_connecting_stops_before_the_handshake(self):
        connection = self._connection(15.0, 1.0, 1.0)

        with self.assertRaises(TimeoutError):
            self._get(connection)

        self.assertEqual(self.tls.handshake_timeouts, [])

    def test_requests_with_a_deadline_open_deadline_connections(self):
        provider = self.mod.GreekSubtitlesProvider()
        for scheme, base in (("https", http.client.HTTPSConnection), ("http", http.client.HTTPConnection)):
            with self.subTest(scheme=scheme):
                # A ProxyHandler, installed when a proxy is configured, has
                # the same open methods, so pick the handler by type.
                handler_class = {"https": self.mod._DeadlineHTTPSHandler, "http": self.mod._DeadlineHTTPHandler}[scheme]
                handler = next(item for item in provider._opener.handlers if isinstance(item, handler_class))
                request = urllib.request.Request(f"{scheme}://{self.HOST}/")
                with mock.patch.object(handler, "do_open", return_value="response") as do_open:
                    getattr(handler, scheme + "_open")(request)
                    self.assertIs(do_open.call_args.args[0], base)
                    request.deadline = self.deadline
                    getattr(handler, scheme + "_open")(request)
                    opened = do_open.call_args.args[0]
                self.assertTrue(issubclass(opened, base))
                self.assertIsNot(opened, base)


class GreekSubtitlesLocalServerDeadlineTests(unittest.TestCase):
    """The real opener and http.client against a local server."""

    def setUp(self):
        self.mod = _load_provider_module()
        self.server = _StallingServer()
        self.addCleanup(self.server.close)

    def test_answer_inside_the_deadline_is_read(self):
        provider = self.mod.GreekSubtitlesProvider()

        self.assertEqual(provider._http_get(self.server.url + "/fast", deadline=time.monotonic() + 5), b"<html>ok</html>")

    def test_held_request_ends_at_the_deadline(self):
        provider = self.mod.GreekSubtitlesProvider()
        started = time.monotonic()

        with self.assertRaises((TimeoutError, urllib.error.URLError)) as caught:
            provider._http_get(self.server.url + "/slow", deadline=time.monotonic() + 0.3)

        self.assertTrue(self.mod._timed_out(caught.exception))
        self.assertLess(time.monotonic() - started, 2.0)


class GreekSubtitlesTimeBudgetTests(unittest.TestCase):
    EPISODE = {"kind": "episode", "series": "Slow Show", "season": 1, "episode": 1}
    GREEK = [{"alpha3": "ell", "alpha2": "el"}]
    CONFIG = {"request_delay_ms": 0}
    DOWNLOAD = {
        "provider": "greeksubtitles",
        "schema": 1,
        "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
        "page_url": "http://subtitles.gr/subtitles/x/1/",
        "filename": "raw.srt",
    }

    def setUp(self):
        self.mod = _load_provider_module()
        self.clock = _FakeClock()
        self.started = self.clock()
        self.deadline = self.started + self.mod.SEARCH_BUDGET_SECONDS
        self.sleeps = []

        def fake_sleep(seconds):
            self.sleeps.append(seconds)
            self.clock.advance(seconds)

        patcher = mock.patch.object(self.mod.time, "sleep", side_effect=fake_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _paged_site(self, page_count, **kwargs):
        """page_count result pages chained by Next links, one Greek row each."""
        pages = {}
        url = self.mod.search_url_for("Slow Show S01E01")
        for index in range(page_count):
            next_page = index + 1 if index + 1 < page_count else None
            body = _result_page(9000 + index, next_page)
            pages[url] = body
            url = self.mod.parse_search_page(body, url)["next_url"]
        return _TimedOpener(self.clock, pages, **kwargs)

    def _provider(self, opener):
        provider = self.mod.GreekSubtitlesProvider()
        provider._opener = opener
        provider._monotonic = self.clock
        return provider

    def _assert_requests_fit_the_budget(self, opener):
        self.assertLessEqual(self.clock() - self.started, self.mod.SEARCH_BUDGET_SECONDS)
        for call in opener.calls:
            remaining = self.deadline - call["started"]
            self.assertGreater(remaining, 0, call["url"])
            self.assertGreater(call["timeout"], 0, call["url"])
            self.assertLessEqual(call["timeout"], remaining, call["url"])

    def test_timeouts_and_retries_fail_fast(self):
        self.assertLessEqual(self.mod.HTTP_TIMEOUT_SECONDS, 15)
        self.assertLessEqual(self.mod.HTTP_RETRIES, 1)
        # Discover stops waiting at 20 seconds on an install set to the older
        # limit, and a real request runs a second or two past its socket
        # timeout because DNS, connect and TLS are timed separately.
        self.assertLessEqual(self.mod.SEARCH_BUDGET_SECONDS, 15)
        # Discover's download job waits 60 seconds for the whole call.
        self.assertGreater(self.mod.DOWNLOAD_TIMEOUT_SECONDS, self.mod.HTTP_TIMEOUT_SECONDS)
        self.assertLess(self.mod.DOWNLOAD_TIMEOUT_SECONDS, 60)

    def test_timed_out_covers_read_and_connect_timeouts_only(self):
        timed_out = self.mod._timed_out
        self.assertTrue(timed_out(TimeoutError("timed out")))
        self.assertTrue(timed_out(socket.timeout("timed out")))
        self.assertTrue(timed_out(urllib.error.URLError(TimeoutError("timed out"))))
        self.assertFalse(timed_out(urllib.error.URLError(ConnectionRefusedError())))
        self.assertFalse(timed_out(urllib.error.HTTPError("https://x", 503, "Busy", None, None)))

    def test_search_requests_use_the_short_timeout(self):
        opener = self._paged_site(1)

        self.assertEqual(len(self._provider(opener).search(self.EPISODE, self.GREEK, self.CONFIG)), 1)

        self.assertEqual(len(opener.calls), 1)
        self.assertLessEqual(opener.calls[0]["timeout"], 15)

    def test_download_waits_out_a_slow_first_hop(self):
        # getp.php has answered its redirect after 22 and 35 seconds, well past
        # a search page's timeout, with the file itself following in under one.
        url = self.DOWNLOAD["download_url"]
        opener = _TimedOpener(
            self.clock,
            {url: b"1\n00:00:01,000 --> 00:00:02,000\nLine\n"},
            latency={url: 35.2},
        )

        content = self._provider(opener).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(base64.b64decode(content["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nLine\n")
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(opener.calls[0]["timeout"], self.mod.DOWNLOAD_TIMEOUT_SECONDS)

    def test_download_that_times_out_ends_inside_the_discover_job_wait(self):
        opener = _TimedOpener(self.clock, {}, default_latency=600.0)

        with self.assertRaises(TimeoutError):
            self._provider(opener).download(self.DOWNLOAD, self.GREEK[0], {})

        # One full wait and no second one: Discover's download job gives the
        # provider 60 seconds in all, and the host's own work needs some of
        # that.
        self.assertEqual(len(opener.calls), 1)
        self.assertLessEqual(self.clock() - self.started, self.mod.DOWNLOAD_TIMEOUT_SECONDS)
        self.assertLessEqual(self.mod.DOWNLOAD_TIMEOUT_SECONDS, 55)

    def test_download_retries_a_fast_connection_failure(self):
        clock = self.clock
        body = b"1\n00:00:01,000 --> 00:00:02,000\nLine\n"

        class RefusedThenAnswers:
            def __init__(self):
                self.calls = []

            def open(self, request, timeout):
                self.calls.append({"url": request.full_url, "timeout": timeout, "started": clock()})
                if len(self.calls) == 1:
                    clock.advance(0.5)
                    raise urllib.error.URLError(ConnectionRefusedError())
                clock.advance(1.0)
                return _FakeResponse(body)

        opener = RefusedThenAnswers()

        content = self._provider(opener).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(base64.b64decode(content["content_b64"]), body)
        self.assertEqual(len(opener.calls), 2)
        download_deadline = self.started + self.mod.DOWNLOAD_TIMEOUT_SECONDS
        second = opener.calls[1]
        self.assertLessEqual(second["timeout"], download_deadline - second["started"])

    def _provider_behind(self, host):
        provider = self.mod.GreekSubtitlesProvider()
        provider._monotonic = self.clock
        provider._opener.add_handler(host)
        return provider

    def test_download_redirect_to_a_stalled_host_ends_by_the_deadline(self):
        # urllib opens a redirect's target with the first hop's timeout, so a
        # slow getp.php answer followed by a silent file host must not get a
        # second full wait.
        url = self.DOWNLOAD["download_url"]
        target = "https://www.greeksubtitles.info/files/1.zip"
        host = _TimedHost(self.clock, {url: (35.0, 302, target), target: (600.0, 200, b"")})

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(host.urls(), [url, target])
        self.assertLessEqual(self.clock() - self.started, self.mod.DOWNLOAD_TIMEOUT_SECONDS)

    def test_download_follows_a_slow_redirect_inside_the_time_left(self):
        url = self.DOWNLOAD["download_url"]
        target = "https://www.greeksubtitles.info/files/1.srt"
        body = b"1\n00:00:01,000 --> 00:00:02,000\nLine\n"
        host = _TimedHost(self.clock, {url: (35.0, 302, target), target: (0.4, 200, body)})

        content = self._provider_behind(host).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(base64.b64decode(content["content_b64"]), body)
        self.assertEqual(host.urls(), [url, target])
        download_deadline = self.started + self.mod.DOWNLOAD_TIMEOUT_SECONDS
        second = host.calls[1]
        self.assertGreater(second["timeout"], 0)
        self.assertLessEqual(second["timeout"], download_deadline - second["started"])

    def test_download_redirect_answered_at_the_deadline_is_not_followed(self):
        url = self.DOWNLOAD["download_url"]
        target = "https://www.greeksubtitles.info/files/1.srt"
        host = _TimedHost(
            self.clock,
            {
                url: (float(self.mod.DOWNLOAD_TIMEOUT_SECONDS), 302, target),
                target: (0.4, 200, b"1\n00:00:01,000 --> 00:00:02,000\nLine\n"),
            },
        )

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(host.urls(), [url])

    def test_search_redirect_hop_stays_inside_the_budget(self):
        url = self.mod.search_url_for("Slow Show S01E01")
        target = f"{self.mod.BASE_URL}/search.php?page=0&name=Slow+Show+S01E01"
        host = _TimedHost(self.clock, {url: (10.0, 301, target), target: (600.0, 200, b"")})

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual(host.urls(), [url, target])
        self._assert_requests_fit_the_budget(host)

    def test_search_page_trickling_in_ends_by_the_deadline(self):
        # A socket timeout bounds each read, not the body, so a page whose
        # bytes keep arriving inside it must still end at the budget.
        url = self.mod.search_url_for("Slow Show S01E01")
        host = _TimedHost(self.clock, {url: (1.0, 200, _Drip(_result_page(9000), 30, 4.0))})

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual(host.urls(), [url])
        self.assertLessEqual(self.clock() - self.started, self.mod.SEARCH_BUDGET_SECONDS)

    def test_later_page_trickling_in_keeps_the_earlier_pages(self):
        url = self.mod.search_url_for("Slow Show S01E01")
        first = _result_page(9000, next_page=1)
        second_url = self.mod.parse_search_page(first, url)["next_url"]
        host = _TimedHost(
            self.clock,
            {
                url: (1.0, 200, first),
                second_url: (1.0, 200, _Drip(_result_page(9001), 30, 4.0)),
            },
        )

        results = self._provider_behind(host).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["9000"])
        self.assertEqual(host.urls(), [url, second_url])
        self.assertLessEqual(self.clock() - self.started, self.mod.SEARCH_BUDGET_SECONDS)

    def test_search_redirect_body_trickling_in_ends_by_the_deadline(self):
        # urllib reads a redirect's own body before following it, with the
        # first hop's socket timeout, so that body must end at the budget too.
        url = self.mod.search_url_for("Slow Show S01E01")
        target = f"{self.mod.BASE_URL}/search.php?page=0&name=Slow+Show+S01E01"
        host = _TimedHost(
            self.clock,
            {
                url: (1.0, 301, (target, _Drip(b"<html>moved</html>" * 20, 30, 4.0))),
                target: (0.5, 200, _result_page(9000)),
            },
        )

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual(host.urls(), [url])
        self.assertLessEqual(self.clock() - self.started, self.mod.SEARCH_BUDGET_SECONDS)

    def test_download_redirect_body_trickling_in_ends_by_its_deadline(self):
        url = self.DOWNLOAD["download_url"]
        target = "https://www.greeksubtitles.info/files/1.srt"
        host = _TimedHost(
            self.clock,
            {
                url: (1.0, 302, (target, _Drip(b"<html>moved</html>" * 20, 30, 10.0))),
                target: (0.4, 200, b"1\n00:00:01,000 --> 00:00:02,000\nLine\n"),
            },
        )

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(host.urls(), [url])
        self.assertLessEqual(self.clock() - self.started, self.mod.DOWNLOAD_TIMEOUT_SECONDS)

    def test_redirect_body_inside_the_budget_is_followed(self):
        url = self.mod.search_url_for("Slow Show S01E01")
        target = f"{self.mod.BASE_URL}/search.php?page=0&name=Slow+Show+S01E01"
        host = _TimedHost(
            self.clock,
            {
                url: (0.5, 301, (target, _Drip(b"<html>moved</html>", 3, 0.5))),
                target: (0.5, 200, _result_page(9000)),
            },
        )

        results = self._provider_behind(host).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["9000"])
        self.assertEqual(host.urls(), [url, target])

    def test_page_sent_in_pieces_inside_the_budget_is_read_whole(self):
        url = self.mod.search_url_for("Slow Show S01E01")
        host = _TimedHost(self.clock, {url: (1.0, 200, _Drip(_result_page(9000), 4, 1.0))})

        results = self._provider_behind(host).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["9000"])

    def test_download_trickling_in_ends_by_its_deadline(self):
        url = self.DOWNLOAD["download_url"]
        body = b"1\n00:00:01,000 --> 00:00:02,000\nLine\n" * 20
        host = _TimedHost(self.clock, {url: (1.0, 200, _Drip(body, 40, 10.0))})

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).download(self.DOWNLOAD, self.GREEK[0], {})

        self.assertEqual(host.urls(), [url])
        self.assertLessEqual(self.clock() - self.started, self.mod.DOWNLOAD_TIMEOUT_SECONDS)

    def test_search_stops_paging_at_the_deadline_and_keeps_earlier_pages(self):
        opener = self._paged_site(self.mod.MAX_PAGES, default_latency=7.0)
        results = self._provider(opener).search(self.EPISODE, self.GREEK, self.CONFIG)

        self._assert_requests_fit_the_budget(opener)
        answered = {call["url"] for call in opener.calls if call["timeout"] >= 7.0}
        self.assertTrue(results)
        self.assertLess(len(answered), self.mod.MAX_PAGES)
        self.assertEqual(len(results), len(answered))

    def test_search_retry_never_runs_past_the_deadline(self):
        opener = self._paged_site(1, default_latency=600.0)

        with self.assertRaises(TimeoutError):
            self._provider(opener).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertLessEqual(len(opener.calls), self.mod.HTTP_RETRIES + 1)
        self._assert_requests_fit_the_budget(opener)

    def test_search_retries_a_fast_failure_inside_the_remaining_budget(self):
        clock = self.clock

        class RefusedThenSilent:
            def __init__(self):
                self.calls = []

            def open(self, request, timeout):
                self.calls.append({"url": request.full_url, "timeout": timeout, "started": clock()})
                if len(self.calls) == 1:
                    clock.advance(0.5)
                    raise urllib.error.URLError(ConnectionRefusedError())
                clock.advance(timeout)
                raise socket.timeout("timed out")

        opener = RefusedThenSilent()

        with self.assertRaises(TimeoutError):
            self._provider(opener).search(self.EPISODE, self.GREEK, self.CONFIG)

        self.assertEqual(len(opener.calls), 2)
        self.assertLess(opener.calls[1]["timeout"], self.mod.HTTP_TIMEOUT_SECONDS)
        self._assert_requests_fit_the_budget(opener)

    def test_alternative_title_timing_out_keeps_the_first_titles_results(self):
        opener = self._paged_site(1)
        alternative = self.mod.search_url_for("Slow Series S01E01")
        opener.pages[alternative] = _result_page(9100)
        opener.latency[alternative] = 600.0
        video = dict(self.EPISODE, alternative_series=["Slow Series"])

        results = self._provider(opener).search(video, self.GREEK, self.CONFIG)

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["9000"])
        self.assertIn(alternative, opener.urls())
        self._assert_requests_fit_the_budget(opener)

    def test_search_still_raises_a_server_error_after_the_first_page(self):
        opener = self._paged_site(2)
        second_url = list(opener.pages)[1]
        opener.pages[second_url] = urllib.error.HTTPError(second_url, 500, "Server Error", None, None)

        with self.assertRaises(urllib.error.HTTPError):
            self._provider(opener).search(self.EPISODE, self.GREEK, self.CONFIG)

    def test_request_delay_never_sleeps_past_the_budget(self):
        opener = self._paged_site(self.mod.MAX_PAGES, default_latency=1.0)
        config = dict(self.CONFIG, request_delay_ms=5000)

        results = self._provider(opener).search(self.EPISODE, self.GREEK, config)

        self.assertTrue(results)
        self.assertTrue(self.sleeps)
        self._assert_requests_fit_the_budget(opener)


if __name__ == "__main__":
    unittest.main()
