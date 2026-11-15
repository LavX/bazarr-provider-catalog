"""Tests of the Google Translate translator plugin. No test reaches Google: every request goes
to a local server that serves pages shaped like the ones the pinned library reads."""

import html
import importlib.util
import json
import random
import threading
import time
import unittest

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from bazarr_plugin_sdk.testing import LoopbackHost, run_conformance

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "google_translate"
FIXTURES = ROOT / "tests" / "fixtures"
KIND = "bazarr.translator.v1"


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("google_translate_plugin", PLUGIN_DIR / "google_translate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def page(name, translation=""):
    template = (FIXTURES / f"google_translate_page_{name}.html").read_text(encoding="utf-8")
    return template.replace("{{translation}}", html.escape(translation)).encode("utf-8")


class Reply:
    """What the fake server answers one request with."""

    def __init__(self, status=200, body=b"", headers=None, stall=0.0, trickle=0.0, trickle_headers=0.0):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.stall = stall
        self.trickle = trickle
        self.trickle_headers = trickle_headers


def translated(request, fixture="t0"):
    return Reply(body=page(fixture, f"T[{request['tl']}]:{request['q']}"))


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # a client that gave up or was cut off is part of what these tests do


class FakeGoogle:
    """A local stand-in for Google's mobile translation page."""

    def __init__(self, behaviour=translated):
        self.behaviour = behaviour
        self.requests = []
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._live = 0
        self.max_live = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            # Google answers over HTTP/1.1 with the connection kept open.
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {key: values[0] for key, values in parse_qs(parts.query).items()}
                request = {"path": parts.path, "tl": query.get("tl"), "sl": query.get("sl"), "q": query.get("q", "")}
                with outer._lock:
                    outer.requests.append(request)
                    outer._live += 1
                    outer.max_live = max(outer.max_live, outer._live)
                counted = True
                try:
                    reply = outer.behaviour(request)
                    if reply.stall:
                        outer.release.wait(reply.stall)
                    # The request is no longer pending once the answer starts to go out.
                    with outer._lock:
                        outer._live -= 1
                        counted = False
                    if reply.trickle_headers:
                        # The status line, one byte at a time, and never the end of the headers.
                        for byte in b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n":
                            if outer.release.is_set():
                                return
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(reply.trickle_headers)
                        outer.release.wait(5)
                        return
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    for key, value in reply.headers.items():
                        self.send_header(key, value)
                    if reply.trickle:
                        self.send_header("Content-Length", "100000")
                        self.end_headers()
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline and not outer.release.is_set():
                            self.wfile.write(b"x")
                            self.wfile.flush()
                            time.sleep(reply.trickle)
                        return
                    self.send_header("Content-Length", str(len(reply.body)))
                    self.end_headers()
                    self.wfile.write(reply.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if counted:
                        with outer._lock:
                            outer._live -= 1

            def log_message(self, *args):
                pass

        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/m"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


class PluginTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plugin_module()

    def setUp(self):
        self.fake = None
        self.hosts = []
        # Retries cost no time in tests.
        for name, value in (("BACKOFF_START", 0.0), ("BACKOFF_JITTER", 0.0)):
            patcher = mock.patch.object(self.module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        for host in self.hosts:
            host.close()
        if self.fake is not None:
            self.fake.close()
        # Requests abandoned by a test end once their server is gone; the next test starts without them.
        deadline = time.monotonic() + 5
        while self.module._abandoned_alive and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.module._abandoned_alive, 0)

    def serve(self, behaviour=translated):
        self.fake = FakeGoogle(behaviour)
        patcher = mock.patch.object(self.module, "PAGE_URL", self.fake.url)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.fake

    def host(self, **kwargs):
        host = LoopbackHost(self.module.GoogleTranslatePlugin, heartbeat_interval=0.05, **kwargs)
        host.wait_ready(10)
        self.hosts.append(host)
        return host

    def translate(self, host, lines, target="de", wait=20, **extra):
        request = {"target": {"code": target}, "lines": lines, **extra}
        return host.request("translate", {"request": request}, wait=wait)

    @staticmethod
    def texts(response):
        return [line["text"] if isinstance(line, dict) else line for line in response.value["lines"]]


class LanguageMapTests(PluginTestCase):
    def code(self, tag):
        return self.module.library_code(tag)

    def test_hebrew_goes_out_as_the_librarys_iw(self):
        self.assertEqual(self.code("he"), "iw")

    def test_chinese_follows_its_script_and_region(self):
        for tag in ("zh", "zh-Hans", "zh-CN", "zh-Hans-CN", "zh-SG"):
            self.assertEqual(self.code(tag), "zh-CN", tag)
        for tag in ("zh-Hant", "zh-TW", "zh-Hant-TW", "zh-HK", "zh-MO"):
            self.assertEqual(self.code(tag), "zh-TW", tag)

    def test_regional_variants_go_to_the_base_the_library_lists(self):
        self.assertEqual(self.code("pt-BR"), "pt")
        self.assertEqual(self.code("pt-PT"), "pt")
        self.assertEqual(self.code("es-419"), "es")
        self.assertEqual(self.code("sr-Latn"), "sr")

    def test_other_retired_and_split_codes_follow_the_library(self):
        self.assertEqual(self.code("jv"), "jw")
        self.assertEqual(self.code("nb"), "no")

    def test_a_language_google_lacks_is_none_never_an_exception(self):
        for tag in ("cnr", "nn", "sh", "tlh", "xx", "und", "auto", "", "x", "en-", "e1", "en-US-x-twain", None, 5, ["en"]):
            self.assertIsNone(self.code(tag), tag)

    def test_every_language_bazarr_can_send_is_mapped_or_declined(self):
        fixture = json.loads((FIXTURES / "google_translate_bazarr_targets.json").read_text(encoding="utf-8"))
        codes = self.module.LIBRARY_CODES
        declined = set()
        for bazarr_code, tag in fixture["targets"].items():
            code = self.code(tag)
            if code is None:
                declined.add(tag)
            else:
                self.assertIn(code, codes, f"{bazarr_code} -> {tag} -> {code}")
        # The languages Google has no code for. A change here is a change in what the free engine can do.
        self.assertEqual(
            declined,
            {
                "aa", "ab", "ae", "an", "av", "ba", "bi", "bo", "br", "ce", "ch", "cnr", "cr", "cu", "cv", "dz",
                "ff", "fj", "fo", "gv", "ho", "hz", "ia", "ie", "ii", "ik", "io", "iu", "kg", "ki", "kj", "kl",
                "kr", "ks", "kv", "kw", "li", "lu", "mh", "na", "nd", "ng", "nn", "nr", "nv", "oc", "oj", "os",
                "pi", "rm", "rn", "sc", "se", "sg", "sh", "ss", "tn", "to", "tw", "ty", "ve", "vo", "wa", "wo",
                "za",
            },
        )

    def test_the_mapped_codes_are_read_from_the_pinned_library(self):
        from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES

        self.assertEqual(self.module.LIBRARY_CODES, frozenset(GOOGLE_LANGUAGES_TO_CODES.values()))
        self.assertIn("iw", self.module.LIBRARY_CODES)
        self.assertNotIn("he", self.module.LIBRARY_CODES)


class TagTests(PluginTestCase):
    def cue(self, text, target="de"):
        return self.module._translate_cue(text, target, lambda: False)

    def sent(self):
        return [request["q"] for request in self.fake.requests]

    def test_tags_around_a_whole_cue_are_taken_off_and_put_back(self):
        self.serve()
        cue = self.cue("<i>Hello there</i>")
        self.assertEqual(cue.text, "<i>T[de]:Hello there</i>")
        self.assertEqual(self.sent(), ["Hello there"])
        self.assertFalse(cue.tags_lost)

    def test_nested_tags_come_back_in_order(self):
        self.serve()
        self.assertEqual(self.cue("<b><i>Hi</i></b>").text, "<b><i>T[de]:Hi</i></b>")
        self.assertEqual(self.cue("<B>Hi</B>").text, "<B>T[de]:Hi</B>")

    def test_tags_on_each_line_are_restored_on_each_line(self):
        self.serve()
        cue = self.cue("<i>First line</i>\n<i>Second line</i>")
        self.assertEqual(cue.text, "<i>T[de]:First line</i>\n<i>T[de]:Second line</i>")
        self.assertEqual(sorted(self.sent()), ["First line", "Second line"])

    def test_a_tag_that_spans_lines_wraps_the_translation(self):
        self.serve()
        cue = self.cue("<i>First line\nsecond line</i>")
        self.assertEqual(cue.text, "<i>T[de]:First line\nsecond line</i>")

    def test_tags_in_the_middle_of_a_line_cannot_be_restored(self):
        self.serve()
        cue = self.cue("Hello <i>big</i> world")
        self.assertEqual(cue.text, "T[de]:Hello big world")
        self.assertTrue(cue.tags_lost)
        self.assertEqual(self.sent(), ["Hello big world"])

    def test_a_trailing_newline_is_kept(self):
        self.serve()
        self.assertEqual(self.cue("Hello\n").text, "T[de]:Hello\n")
        self.assertEqual(self.cue("<i>Hi</i>\n").text, "<i>T[de]:Hi</i>\n")
        self.assertEqual(self.cue("<i>Hi\n</i>").text, "<i>T[de]:Hi\n</i>")

    def test_the_override_blocks_in_front_survive_when_mid_line_tags_are_stripped(self):
        self.serve()
        cue = self.cue("{\\an8}Hello <i>big</i> world")
        self.assertEqual(cue.text, "{\\an8}T[de]:Hello big world")
        self.assertTrue(cue.tags_lost)
        self.assertEqual(self.sent(), ["Hello big world"])
        self.assertEqual(self.cue("<i>{\\an8}Hi <b>x</b> there").text, "{\\an8}T[de]:Hi x there")
        self.assertEqual(self.cue("Hi {\\i1}x{\\i0} there").text, "T[de]:Hi x there")

    def test_an_override_block_is_kept_in_front(self):
        self.serve()
        self.assertEqual(self.cue("{\\an8}Hello").text, "{\\an8}T[de]:Hello")

    def test_a_cue_with_nothing_to_translate_sends_no_request(self):
        self.serve()
        for text in ("", "   ", "♪♪", "<i>♪</i>", "...", "{\\an8}"):
            cue = self.cue(text)
            self.assertEqual(cue.text, text)
            self.assertFalse(cue.translated)
        self.assertEqual(self.fake.requests, [])

    def test_the_text_sent_carries_no_subtitle_tags(self):
        self.serve()
        for text in ("<i>Yes</i>", "<b><u>No</u></b>", "<s>Maybe</s>\n<s>so</s>", "a <i>b</i> c"):
            self.cue(text)
        self.assertTrue(self.fake.requests)
        self.assertFalse([q for q in self.sent() if "<" in q or ">" in q])


class PageTests(PluginTestCase):
    def fetch(self, text="Hello", target="de", tries=1):
        return self.module._translate_text(text, target, lambda: False, tries)

    def test_the_t0_page_is_read(self):
        self.serve(lambda request: translated(request, "t0"))
        self.assertEqual(self.fetch(), "T[de]:Hello")

    def test_the_result_container_page_is_read(self):
        self.serve(lambda request: translated(request, "result_container"))
        self.assertEqual(self.fetch(), "T[de]:Hello")

    def test_an_answer_that_is_the_source_text_comes_back_as_it_is(self):
        self.serve(lambda request: Reply(body=page("t0", request["q"])))
        self.assertEqual(self.fetch("Okay"), "Okay")

    def test_an_answer_that_differs_in_punctuation_is_still_an_answer(self):
        self.serve(lambda request: Reply(body=page("t0", "Okay!")))
        self.assertEqual(self.fetch("Okay"), "Okay!")

    def test_an_echo_with_no_letters_in_it_is_no_answer(self):
        self.serve(lambda request: Reply(body=page("t0", request["q"])))
        self.assertIsNone(self.fetch("..."))

    def test_a_page_with_no_answer_is_no_answer(self):
        self.serve(lambda request: Reply(body=page("no_result")))
        self.assertIsNone(self.fetch())

    def test_a_block_page_with_status_200_is_a_throttle(self):
        self.serve(lambda request: Reply(body=page("blocked")))
        with self.assertRaises(self.module._Throttled):
            self.fetch()

    def test_the_request_is_the_librarys_own(self):
        fake = self.serve()
        self.fetch("Hello", "iw")
        self.assertEqual(fake.requests, [{"path": "/m", "tl": "iw", "sl": "auto", "q": "Hello"}])

    def test_the_answer_is_read_as_utf8(self):
        self.serve(lambda request: Reply(body=page("t0", "שלום 你好")))
        self.assertEqual(self.fetch(), "שלום 你好")


class RetryTests(PluginTestCase):
    def fetch(self, tries=None):
        return self.module._translate_text("Hello", "de", lambda: False, tries)

    def test_a_failed_request_is_retried_until_one_works(self):
        calls = []

        def behaviour(request):
            calls.append(request)
            return Reply(status=500) if len(calls) < 3 else translated(request)

        self.serve(behaviour)
        self.assertEqual(self.fetch(), "T[de]:Hello")
        self.assertEqual(len(calls), 3)

    def test_six_tries_and_then_a_429_is_a_throttle(self):
        fake = self.serve(lambda request: Reply(status=429))
        with self.assertRaises(self.module._Throttled):
            self.fetch()
        self.assertEqual(len(fake.requests), 6)

    def test_six_tries_and_then_a_server_error_is_unreachable(self):
        fake = self.serve(lambda request: Reply(status=503))
        with self.assertRaises(self.module._Unreachable):
            self.fetch()
        self.assertEqual(len(fake.requests), 6)

    def test_a_redirect_to_the_sorry_page_is_a_throttle_and_is_not_followed(self):
        fake = self.serve(lambda request: Reply(status=302, headers={"Location": "http://127.0.0.1:9/sorry/index"}))
        with self.assertRaises(self.module._Throttled):
            self.fetch(tries=1)
        self.assertEqual(len(fake.requests), 1)

    def test_any_other_redirect_is_not_followed_and_is_unreachable(self):
        fake = self.serve(lambda request: Reply(status=301, headers={"Location": "/elsewhere"}))
        with self.assertRaises(self.module._Unreachable):
            self.fetch(tries=1)
        self.assertEqual([request["path"] for request in fake.requests], ["/m"])

    def test_a_4xx_other_than_403_and_429_is_no_answer_and_is_not_retried(self):
        for status in (400, 404, 410, 414):
            fake = self.serve(lambda request, status=status: Reply(status=status))
            with self.assertRaises(self.module._NoAnswer):
                self.fetch()
            self.assertEqual(len(fake.requests), 1, status)
            fake.close()

    def test_a_403_is_retried_and_then_unreachable(self):
        fake = self.serve(lambda request: Reply(status=403))
        with self.assertRaises(self.module._Unreachable):
            self.fetch()
        self.assertEqual(len(fake.requests), 6)

    def test_a_refused_connection_is_unreachable(self):
        fake = self.serve()
        fake.close()
        with self.assertRaises(self.module._Unreachable):
            self.fetch(tries=2)

    def test_the_backoff_doubles_with_jitter(self):
        self.serve(lambda request: Reply(status=500))
        waits = []
        with mock.patch.object(self.module, "BACKOFF_START", 1.0), mock.patch.object(
            self.module, "BACKOFF_JITTER", 1.0
        ), mock.patch.object(self.module, "_wait", lambda seconds, stop: waits.append(seconds)):
            with self.assertRaises(self.module._Unreachable):
                self.fetch()
        self.assertEqual(len(waits), 5)
        for base, wait in zip((1, 2, 4, 8, 16), waits):
            self.assertTrue(base <= wait <= base + 1, (base, wait))

    def test_cancel_stops_a_retry_before_it_is_sent(self):
        fake = self.serve(lambda request: Reply(status=500))
        stop = mock.Mock(side_effect=[False, True])
        with self.assertRaises(self.module._Stopped):
            self.module._translate_text("Hello", "de", stop)
        self.assertEqual(len(fake.requests), 1)


class CeilingTests(PluginTestCase):
    def test_the_ceiling_is_thirty_seconds(self):
        self.assertEqual(self.module.REQUEST_TIMEOUT, 30.0)
        self.assertEqual(self.module.CONCURRENCY, 10)
        self.assertEqual(self.module.MAX_TRIES, 6)

    def test_a_stalled_server_is_cut_off_at_the_ceiling(self):
        self.serve(lambda request: Reply(stall=10))
        with mock.patch.object(self.module, "REQUEST_TIMEOUT", 0.6):
            started = time.monotonic()
            with self.assertRaises(self.module._Unreachable):
                self.module._translate_text("Hello", "de", lambda: False, 1)
        self.assertLess(time.monotonic() - started, 3)

    def test_a_server_that_trickles_its_answer_is_cut_off_at_the_ceiling(self):
        self.serve(lambda request: Reply(trickle=0.05))
        with mock.patch.object(self.module, "REQUEST_TIMEOUT", 0.6):
            started = time.monotonic()
            with self.assertRaises(self.module._Unreachable):
                self.module._translate_text("Hello", "de", lambda: False, 1)
        self.assertLess(time.monotonic() - started, 3)

    def test_a_server_that_trickles_its_headers_is_cut_off_at_the_ceiling(self):
        self.serve(lambda request: Reply(trickle_headers=0.05))
        with mock.patch.object(self.module, "REQUEST_TIMEOUT", 0.6):
            started = time.monotonic()
            with self.assertRaises(self.module._Unreachable):
                self.module._translate_text("Hello", "de", lambda: False, 1)
        self.assertLess(time.monotonic() - started, 4)

    def test_a_lookup_or_connect_that_never_returns_is_cut_off_at_the_ceiling(self):
        release = threading.Event()

        def hang(*args, **kwargs):
            release.wait(10)
            raise AssertionError("the abandoned request was waited for")

        try:
            with mock.patch.object(self.module.requests, "get", hang), mock.patch.object(
                self.module, "REQUEST_TIMEOUT", 0.5
            ):
                started = time.monotonic()
                with self.assertRaises(self.module._Unreachable):
                    self.module._translate_text("Hello", "de", lambda: False, 1)
            self.assertLess(time.monotonic() - started, 4)
        finally:
            release.set()

    def test_a_timeout_is_retried_like_a_failed_request(self):
        fake = self.serve(lambda request: Reply(stall=10))
        with mock.patch.object(self.module, "REQUEST_TIMEOUT", 0.3):
            with self.assertRaises(self.module._Unreachable):
                self.module._translate_text("Hello", "de", lambda: False, 3)
        self.assertEqual(len(fake.requests), 3)

    def test_cancel_returns_long_before_a_stalled_server_would(self):
        fake = self.serve(lambda request: Reply(stall=20))
        host = self.host()
        response = host.submit("translate", {"request": {"target": {"code": "de"}, "lines": [{"text": "Hello"}] * 4}})
        deadline = time.monotonic() + 5
        while not fake.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(fake.requests, "the plugin never sent a request")
        host.cancel(response)
        started = time.monotonic()
        response.wait(10)
        self.assertEqual(response.code, "cancelled")
        self.assertLess(time.monotonic() - started, self.module.REQUEST_TIMEOUT)
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse([call for call in host.calls if call[1] in ("reserve", "checkpoint")])
        # No retry is sent after the cancel.
        count = len(fake.requests)
        fake.release.set()
        time.sleep(0.3)
        self.assertEqual(len(fake.requests), count)


class SlotTests(PluginTestCase):
    """An abandoned request keeps its slot until its own thread ends."""

    def setUp(self):
        super().setUp()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.started = []

        def hang(url, params, abandoned):
            self.started.append(params["q"])
            self.release.wait(10)
            raise self.module._Unreachable()

        for target, value in (
            (self.module, ("_fetch_page", hang)),
            (self.module, ("REQUEST_TIMEOUT", 0.3)),
            (self.module, ("_slots", threading.BoundedSemaphore(2))),
            (self.module, ("MAX_ABANDONED", 2)),
        ):
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.release.set()
        super().tearDown()

    def attempt(self, text):
        try:
            return self.module._translate_text(text, "de", lambda: False, 1)
        except Exception as error:
            return error

    def test_an_abandoned_request_still_holds_its_slot_and_counts_as_abandoned(self):
        first = self.attempt("one")
        self.assertIsInstance(first, self.module._Unreachable)
        self.assertEqual(self.module._abandoned_alive, 1)
        self.assertFalse(self.module._slots.acquire(blocking=False) and self.module._slots.acquire(blocking=False))

    def test_at_the_bound_new_requests_are_refused_and_no_more_threads_start(self):
        for text in ("one", "two"):
            self.assertIsInstance(self.attempt(text), self.module._Unreachable)
        self.assertEqual(self.module._abandoned_alive, 2)
        threads = threading.active_count()
        self.assertIsInstance(self.attempt("three"), self.module._Unreachable)
        self.assertEqual(self.started, ["one", "two"])
        self.assertEqual(threading.active_count(), threads)

    def test_slots_and_the_count_come_back_when_the_threads_end(self):
        for text in ("one", "two"):
            self.attempt(text)
        self.release.set()
        deadline = time.monotonic() + 3
        while self.module._abandoned_alive and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.module._abandoned_alive, 0)
        self.assertTrue(self.module._slots.acquire(blocking=False))
        self.assertTrue(self.module._slots.acquire(blocking=False))
        self.module._slots.release()
        self.module._slots.release()

    def test_a_request_waiting_for_a_slot_gives_up_at_the_deadline_or_on_cancel(self):
        with mock.patch.object(self.module, "MAX_ABANDONED", 10):
            for text in ("one", "two"):
                self.attempt(text)  # both slots are now held by abandoned threads
            started = time.monotonic()
            self.assertIsInstance(self.attempt("three"), self.module._Unreachable)
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(self.started, ["one", "two"])
            stop = mock.Mock(side_effect=[False, True])
            result = None
            try:
                self.module._translate_text("four", "de", stop, 1)
            except Exception as error:
                result = error
            self.assertIsInstance(result, self.module._Stopped)

    def test_in_flight_requests_never_exceed_the_slots_even_with_retries(self):
        live = []
        peak = [0]
        lock = threading.Lock()
        release = threading.Event()
        self.addCleanup(release.set)

        def slow(url, params, abandoned):
            with lock:
                live.append(1)
                peak[0] = max(peak[0], len(live))
            try:
                release.wait(0.5)
                raise self.module._Unreachable()
            finally:
                with lock:
                    live.pop()

        with mock.patch.object(self.module, "_fetch_page", slow), mock.patch.object(
            self.module, "REQUEST_TIMEOUT", 0.2
        ), mock.patch.object(self.module, "MAX_ABANDONED", 100):
            threads = [threading.Thread(target=self.attempt, args=(f"t{i}",)) for i in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(15)
        self.assertLessEqual(peak[0], 2)

    def test_a_cancel_is_noticed_during_a_request(self):
        with mock.patch.object(self.module, "REQUEST_TIMEOUT", 20):
            flag = threading.Event()
            threading.Timer(0.3, flag.set).start()
            started = time.monotonic()
            with self.assertRaises(self.module._Stopped):
                self.module._translate_text("one", "de", flag.is_set, 1)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(self.module._abandoned_alive, 1)


class OperationTests(PluginTestCase):
    def test_describe_names_no_back_ends_and_the_mapped_targets(self):
        host = self.host()
        described = host.request("describe").value
        self.assertEqual(described["backend_types"], [])
        self.assertEqual(described["source_languages"], ["auto"])
        targets = described["target_languages"]
        for tag in ("he", "zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "pt-BR", "en"):
            if tag == "pt-BR":
                self.assertNotIn(tag, targets)  # listed by its base, pt, which the plugin also takes
            else:
                self.assertIn(tag, targets, tag)
        self.assertNotIn("iw", targets)
        self.assertNotIn("jw", targets)
        for tag in targets:
            self.assertIsNotNone(self.module.library_code(tag), tag)

    def test_describe_lists_both_norwegian_codes_and_the_codes_bazarr_sends(self):
        targets = self.host().request("describe").value["target_languages"]
        for tag in ("no", "nb", "he", "jv"):
            self.assertIn(tag, targets, tag)
        self.assertNotIn("nn", targets)
        fixture = json.loads((FIXTURES / "google_translate_bazarr_targets.json").read_text(encoding="utf-8"))
        for tag in fixture["targets"].values():
            if self.module.library_code(tag) is not None:
                base = tag.split("-")[0]
                self.assertTrue(tag in targets or base in targets or tag.startswith("zh"), tag)

    def test_options_is_empty(self):
        host = self.host()
        self.assertEqual(host.request("options", {"field": "model", "config": {}}).value, [])

    def test_validate_accepts_the_empty_configuration_without_the_network(self):
        fake = self.serve()
        host = self.host()
        self.assertEqual(host.request("validate", {"config": {}}).value, {})
        self.assertEqual(fake.requests, [])

    def test_validate_names_every_field_it_refuses(self):
        host = self.host()
        response = host.request("validate", {"config": {"api_key": "x", "region": "eu"}})
        self.assertEqual(response.code, "invalid_config")
        self.assertEqual(sorted(response.error["field_errors"]), ["api_key", "region"])
        self.assertIn("api_key", response.error["message"])

    def test_validate_refuses_a_configuration_that_is_not_an_object(self):
        host = self.host()
        self.assertEqual(host.request("validate", {"config": ["x"]}).code, "invalid_config")

    def test_migrate_is_the_identity_at_version_one(self):
        host = self.host()
        response = host.request("migrate", {"from_version": 1, "settings": {}, "connections": []})
        self.assertEqual(response.value, {"settings": {}, "connections": []})
        self.assertEqual(host.request("migrate", {"from_version": 2, "settings": {}, "connections": []}).code, "invalid_config")

    def test_test_makes_one_real_call_and_reserves_nothing(self):
        fake = self.serve()
        host = self.host()
        response = host.request("test", {"config": {}})
        self.assertEqual(response.value["ok"], True)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.requests[0]["q"], self.module.TEST_PHRASE)
        self.assertFalse([call for call in host.calls if call[1] in ("reserve", "usage")])

    def test_test_reports_a_throttle_as_rate_limited(self):
        fake = self.serve(lambda request: Reply(status=429))
        response = self.host().request("test", {"config": {}})
        self.assertEqual(response.code, "rate_limited")
        self.assertEqual(len(fake.requests), 1)  # a test is one try

    def test_test_reports_no_route_as_dependency_unavailable(self):
        self.serve(lambda request: Reply(status=500))
        self.assertEqual(self.host().request("test", {"config": {}}).code, "dependency_unavailable")

    def test_test_reports_an_empty_answer_as_internal(self):
        self.serve(lambda request: Reply(body=page("no_result")))
        response = self.host().request("test", {"config": {}})
        self.assertEqual(response.code, "internal")
        self.assertIn("no usable text", response.error["message"])


class TranslateTests(PluginTestCase):
    def test_lines_come_back_in_input_order_with_their_positions(self):
        self.serve()
        lines = [{"position": index, "text": f"Line {index}"} for index in range(25)]
        response = self.translate(self.host(), lines)
        self.assertTrue(response.ok, response.error)
        self.assertEqual(
            response.value["lines"], [{"position": index, "text": f"T[de]:Line {index}"} for index in range(25)]
        )
        self.assertEqual(response.value["status"], "complete")

    def test_plain_string_lines_come_back_as_strings(self):
        self.serve()
        response = self.translate(self.host(), ["One", "Two"])
        self.assertEqual(response.value["lines"], ["T[de]:One", "T[de]:Two"])

    def test_the_target_is_mapped_before_it_is_sent(self):
        fake = self.serve()
        host = self.host()
        for tag, expected in (("he", "iw"), ("zh-Hant", "zh-TW"), ("zh", "zh-CN"), ("pt-BR", "pt"), ("es-419", "es")):
            fake.requests.clear()
            self.translate(host, ["Hello"], target=tag)
            self.assertEqual([request["tl"] for request in fake.requests], [expected], tag)

    def test_hebrew_text_survives_the_round_trip(self):
        self.serve(lambda request: Reply(body=page("t0", "שלום")))
        response = self.translate(self.host(), ["Hello"], target="he")
        self.assertEqual(self.texts(response), ["שלום"])

    def test_a_language_google_lacks_is_declined_without_a_request(self):
        fake = self.serve()
        host = self.host()
        for tag in ("cnr", "nn", "xx"):
            response = self.translate(host, ["Hello"], target=tag)
            self.assertEqual(response.code, "declined", tag)
            self.assertEqual(response.error["reason"], "language_unsupported")
        self.assertEqual(fake.requests, [])

    def test_a_request_with_no_target_is_declined_too(self):
        self.serve()
        response = self.host().request("translate", {"request": {"lines": ["Hello"]}})
        self.assertEqual(response.code, "declined")
        self.assertEqual(response.error["reason"], "language_unsupported")

    def test_a_request_with_no_lines_is_an_internal_error_not_a_crash(self):
        self.serve()
        response = self.host().request("translate", {"request": {"target": {"code": "de"}}})
        self.assertEqual(response.code, "internal")

    def test_tags_are_restored_and_the_loss_is_counted_in_the_message(self):
        self.serve()
        lines = ["<i>Kept</i>", "Lost <b>this</b> one", "Plain"]
        response = self.translate(self.host(), lines)
        self.assertEqual(self.texts(response), ["<i>T[de]:Kept</i>", "T[de]:Lost this one", "T[de]:Plain"])
        self.assertIn("1 cues came back without their tags", response.value["message"])

    def test_cues_with_nothing_to_translate_pass_through(self):
        fake = self.serve()
        response = self.translate(self.host(), ["♪♪", "", "Hello"])
        self.assertEqual(self.texts(response), ["♪♪", "", "T[de]:Hello"])
        self.assertEqual(len(fake.requests), 1)

    def test_a_cue_google_has_no_answer_for_keeps_its_source_text(self):
        def behaviour(request):
            return Reply(body=page("no_result")) if request["q"] == "Odd" else translated(request)

        self.serve(behaviour)
        response = self.translate(self.host(), ["Hello", "Odd"])
        self.assertEqual(self.texts(response), ["T[de]:Hello", "Odd"])
        self.assertIn("1 cues kept their source text", response.value["message"])

    def test_a_multiline_cue_that_is_only_partly_translated_is_counted_as_partial(self):
        def behaviour(request):
            return Reply(body=page("no_result")) if request["q"] == "Odd" else translated(request)

        self.serve(behaviour)
        response = self.translate(self.host(), ["<i>Hello</i>\n<i>Odd</i>", "Plain"])
        self.assertEqual(self.texts(response), ["<i>T[de]:Hello</i>\n<i>Odd</i>", "T[de]:Plain"])
        message = response.value["message"]
        self.assertIn("1 cues were only partly translated", message)
        self.assertNotIn("kept their source text because Google gave no answer", message)

    def test_a_cue_with_no_answer_in_any_part_is_counted_as_unanswered_not_partial(self):
        self.serve(lambda request: Reply(body=page("no_result")) if request["q"] in ("Odd", "Even") else translated(request))
        response = self.translate(self.host(), ["<i>Odd</i>\n<i>Even</i>", "Plain"])
        message = response.value["message"]
        self.assertIn("1 cues kept their source text", message)
        self.assertNotIn("partly", message)

    def test_a_client_error_for_one_cue_skips_that_cue_and_the_job_goes_on(self):
        def behaviour(request):
            return Reply(status=400) if request["q"] == "Bad" else translated(request)

        fake = self.serve(behaviour)
        response = self.translate(self.host(), ["Hello", "Bad", "World"])
        self.assertTrue(response.ok, response.error)
        self.assertEqual(self.texts(response), ["T[de]:Hello", "Bad", "T[de]:World"])
        self.assertEqual([request["q"] for request in fake.requests].count("Bad"), 1)  # not retried
        self.assertIn("1 cues kept their source text", response.value["message"])

    def test_a_retired_route_that_answers_4xx_to_everything_is_declined_as_unreachable(self):
        fake = self.serve(lambda request: Reply(status=404))
        response = self.translate(self.host(), ["One", "Two", "Three"])
        self.assertEqual(response.code, "declined")
        self.assertEqual(response.error["reason"], "server_unreachable")
        self.assertEqual(len(fake.requests), 3)  # no retries

    def test_the_same_holds_when_earlier_cues_were_resumed(self):
        self.serve(lambda request: Reply(status=404))
        lines = ["One", "Two", "Three"]
        resumed = self.module.pack_cues({0: "EARLIER"}, "de", self.module.source_digest(lines))
        response = self.translate(self.host(), lines, checkpoints=[resumed])
        self.assertEqual(response.code, "declined")
        self.assertEqual(response.error["reason"], "server_unreachable")

    def test_a_resumed_job_with_nothing_left_to_ask_is_complete(self):
        fake = self.serve(lambda request: Reply(status=404))
        lines = ["One", "Two"]
        resumed = self.module.pack_cues({0: "A", 1: "B"}, "de", self.module.source_digest(lines))
        response = self.translate(self.host(), lines, checkpoints=[resumed])
        self.assertEqual(self.texts(response), ["A", "B"])
        self.assertEqual(fake.requests, [])

    def test_a_page_with_no_answer_for_every_cue_stays_an_internal_error_when_not_refused(self):
        self.serve(lambda request: Reply(body=page("no_result")))
        lines = ["One", "Two"]
        resumed = self.module.pack_cues({}, "de", self.module.source_digest(lines))
        response = self.translate(self.host(), lines, checkpoints=[resumed])
        self.assertEqual(response.code, "internal")

    def test_no_usable_text_at_all_is_an_internal_error(self):
        self.serve(lambda request: Reply(body=page("no_result")))
        response = self.translate(self.host(), ["One", "Two"])
        self.assertEqual(response.code, "internal")
        self.assertIn("Google returned no usable text", response.error["message"])

    def test_a_job_of_only_untranslatable_cues_is_complete(self):
        fake = self.serve()
        response = self.translate(self.host(), ["♪", "..."])
        self.assertTrue(response.ok)
        self.assertEqual(fake.requests, [])

    def test_tokens_and_cost_are_zero_and_nothing_is_reserved(self):
        self.serve()
        host = self.host()
        response = self.translate(host, ["Hello"])
        self.assertEqual((response.value["tokens"], response.value["cost"]), (0, 0.0))
        self.assertFalse([call for call in host.calls if call[1] in ("reserve", "usage")])

    def test_progress_is_sent_and_the_plugin_heartbeats_itself(self):
        self.serve(lambda request: Reply(body=page("t0", "x"), stall=0.15))
        host = self.host()
        beats = []
        original = self.module.PluginContext.heartbeat

        def spy(context):
            beats.append(time.monotonic())
            return original(context)

        with mock.patch.object(self.module.PluginContext, "heartbeat", spy):
            response = self.translate(host, [f"Line {index}" for index in range(30)])
        self.assertTrue(response.ok)
        self.assertTrue(response.progress)
        self.assertEqual(response.progress[-1]["data"], {"done": 30, "total": 30})
        # The plugin's own calls, about one per half interval (25 ms here) while it waits on Google.
        self.assertGreaterEqual(len(beats), 10)
        gaps = [later - earlier for earlier, later in zip(beats, beats[1:])]
        self.assertLess(max(gaps), 1.0)

    def test_a_throttle_after_the_retries_is_rate_limited_with_a_retry_time(self):
        self.serve(lambda request: Reply(status=429))
        response = self.translate(self.host(), ["Hello", "World"])
        self.assertEqual(response.code, "rate_limited")
        self.assertEqual(response.error["retry_after"], self.module.THROTTLE_RETRY_AFTER)
        self.assertGreaterEqual(self.module.THROTTLE_RETRY_AFTER, 300)

    def test_no_route_to_google_is_declined_as_unreachable(self):
        self.serve(lambda request: Reply(status=502))
        response = self.translate(self.host(), ["Hello"])
        self.assertEqual(response.code, "declined")
        self.assertEqual(response.error["reason"], "server_unreachable")

    def test_a_failure_in_the_library_reaches_the_host_as_a_typed_error_without_raw_text(self):
        self.serve()
        with mock.patch.object(self.module, "_Page", side_effect=RuntimeError("secret raw detail")):
            response = self.translate(self.host(), ["Hello"])
        self.assertEqual(response.code, "internal")
        self.assertNotIn("secret raw detail", json.dumps(response.error))
        self.assertIn("RuntimeError", response.error["message"])

    def test_requests_in_flight_never_exceed_the_limit_even_across_jobs(self):
        fake = self.serve(lambda request: Reply(body=page("t0", "x"), stall=0.1))
        host = self.host()
        lines = [f"Line {index}" for index in range(40)]
        responses = [
            host.submit("translate", {"request": {"target": {"code": "de"}, "lines": lines}}) for _ in range(3)
        ]
        for response in responses:
            response.wait(30)
            self.assertTrue(response.ok, response.error)
        self.assertLessEqual(fake.max_live, 10)
        self.assertGreaterEqual(fake.max_live, 5)
        self.assertEqual(len(fake.requests), 120)


class CheckpointTests(PluginTestCase):
    def checkpoints(self, host):
        return [params["data"] for _at, name, params in host.calls if name == "checkpoint"]

    def cues(self, data, lines=None, target="de"):
        """The cues a checkpoint holds, read as a retry of `lines` into `target` would read them."""
        if lines is None:
            return self.module._unpack_cues(data, data["target"], data["digest"])
        return self.module._unpack_cues(data, target, self.module.source_digest(lines))

    def pack(self, cues, lines, target="de"):
        return self.module.pack_cues(cues, target, self.module.source_digest(lines))

    def test_every_checkpoint_is_cumulative_so_the_latest_alone_is_enough(self):
        self.serve()
        host = self.host()
        response = self.translate(host, [f"Line {index}" for index in range(250)], wait=60)
        self.assertTrue(response.ok, response.error)
        saved = self.checkpoints(host)
        self.assertEqual([item["v"] for item in saved], [2, 2])
        held = [self.cues(item) for item in saved]
        self.assertEqual([len(cues) for cues in held], [100, 200])
        self.assertTrue(set(held[0]) < set(held[1]))
        for key, text in held[1].items():
            self.assertEqual(text, f"T[de]:Line {key}")

    def test_resuming_from_the_latest_checkpoint_alone_sends_only_the_rest(self):
        fake = self.serve()
        lines = [f"Line {index}" for index in range(150)]
        latest = self.pack({index: f"EARLIER {index}" for index in range(100)}, lines)
        host = self.host()
        response = self.translate(host, lines, wait=60, checkpoints=[latest])
        self.assertTrue(response.ok, response.error)
        texts = self.texts(response)
        self.assertEqual(texts[:100], [f"EARLIER {index}" for index in range(100)])
        self.assertEqual(texts[100:], [f"T[de]:Line {index}" for index in range(100, 150)])
        self.assertEqual(len(fake.requests), 50)
        self.assertEqual(len(self.checkpoints(host)), 0)  # 50 new cues, below the cadence

    def test_a_retry_after_a_resume_stores_everything_done_so_far(self):
        self.serve()
        host = self.host()
        lines = [f"Line {index}" for index in range(100)]
        latest = self.pack({index: f"EARLIER {index}" for index in range(30)}, lines)
        with mock.patch.object(self.module, "CHECKPOINT_EVERY", 50):
            response = self.translate(host, lines, checkpoints=[latest], wait=60)
        self.assertTrue(response.ok, response.error)
        held = self.cues(self.checkpoints(host)[-1])
        self.assertEqual(held["0"], "EARLIER 0")
        self.assertEqual(held["29"], "EARLIER 29")
        self.assertTrue(any(text.startswith("T[de]:") for text in held.values()))
        self.assertEqual(len(held), 80)  # the 30 resumed and the 50 that triggered the checkpoint

    def test_the_latest_of_several_checkpoints_wins_and_the_single_form_is_read(self):
        fake = self.serve()
        lines = ["a", "b", "c", "d"]
        older = self.pack({0: "OLD 0", 1: "OLD 1"}, lines)
        newer = self.pack({1: "NEW 1", 2: "NEW 2"}, lines)
        response = self.translate(self.host(), lines, checkpoints=[older, newer])
        self.assertEqual(self.texts(response), ["OLD 0", "NEW 1", "NEW 2", "T[de]:d"])
        fake.requests.clear()
        response = self.translate(self.host(), lines[:2], checkpoint=self.pack({1: "NEW 1"}, lines[:2]))
        self.assertEqual(self.texts(response), ["T[de]:a", "NEW 1"])

    def test_a_checkpoint_in_a_wrapper_or_with_a_bad_index_is_read_safely(self):
        fake = self.serve()
        lines = ["a", "b"]
        saved = [
            {"data": self.pack({0: "FROM WRAPPER", 99: "OUT OF RANGE"}, lines)},
            self.pack({1: 5}, lines),
            "junk",
            {"v": 1, "cues": {"1": "OLD FORMAT"}},
            {"v": 2, "cues_z": "not base64 or zlib"},
        ]
        response = self.translate(self.host(), lines, checkpoints=saved)
        self.assertEqual(self.texts(response), ["FROM WRAPPER", "T[de]:b"])
        self.assertEqual(len(fake.requests), 1)

    def test_a_long_job_stays_under_the_frame_cap(self):
        # Realistic text: no two cues alike, so the compression has little to work with.
        rng = random.Random(7)
        letters = "\u05d0\u05d1\u05d2\u05d3\u05d4\u05d5\u05d6\u05d7\u05d8\u05d9\u05db\u05dc\u05de\u05e0\u05e1\u05e2\u05e4\u05e6\u05e7\u05e8\u05e9\u05ea "
        cues = {index: "".join(rng.choice(letters) for _ in range(rng.randint(15, 60))) for index in range(12000)}
        data = self.pack(cues, ["x"])
        self.assertLess(len(json.dumps(data)), 1024 * 1024)
        self.assertEqual(self.cues(data), {str(key): value for key, value in cues.items()})

    def test_a_checkpoint_for_another_target_or_another_file_is_ignored(self):
        fake = self.serve()
        lines = ["a", "b", "c"]
        for stale in (
            self.pack({0: "STALE"}, lines, target="fr"),
            self.pack({0: "STALE"}, ["a", "b", "EDITED"]),
            {**self.pack({0: "STALE"}, lines), "digest": None},
            {key: value for key, value in self.pack({0: "STALE"}, lines).items() if key != "target"},
        ):
            fake.requests.clear()
            response = self.translate(self.host(), lines, checkpoints=[stale])
            self.assertEqual(self.texts(response), ["T[de]:a", "T[de]:b", "T[de]:c"])
            self.assertEqual(len(fake.requests), 3)

    def test_a_checkpoint_carries_the_target_code_sent_to_google_and_the_source_digest(self):
        self.serve()
        host = self.host()
        lines = [f"Line {index}" for index in range(10)]
        with mock.patch.object(self.module, "CHECKPOINT_EVERY", 5):
            self.translate(host, lines, target="he")
        data = self.checkpoints(host)[-1]
        self.assertEqual((data["target"], data["digest"]), ("iw", self.module.source_digest(lines)))
        self.assertEqual(len(self.cues(data, lines, target="iw")), 10)

    def test_a_checkpoint_that_cannot_be_sent_does_not_fail_the_job(self):
        self.serve()
        context = self.module.PluginContext
        for error in (ValueError("frame over the cap"), OSError("pipe"), self.module.PluginError("internal", "no")):
            with mock.patch.object(context, "checkpoint_sync", side_effect=error), mock.patch.object(
                self.module, "CHECKPOINT_EVERY", 3
            ):
                response = self.translate(self.host(), [f"Line {index}" for index in range(10)])
            self.assertTrue(response.ok, (error, response.error))
            self.assertEqual(self.texts(response)[9], "T[de]:Line 9")

    def test_a_failed_job_stores_what_it_finished_before_it_gives_up(self):
        def behaviour(request):
            if request["q"] == "Line 0":
                return Reply(status=500, stall=0.4)
            return translated(request)

        self.serve(behaviour)
        host = self.host()
        with mock.patch.object(self.module, "MAX_TRIES", 2):
            response = self.translate(host, [f"Line {index}" for index in range(60)], wait=30)
        self.assertEqual(response.code, "declined")
        saved = self.checkpoints(host)
        self.assertTrue(saved)
        held = self.cues(saved[-1])
        self.assertNotIn("0", held)
        self.assertGreater(len(held), 5)
        for key, text in held.items():
            self.assertEqual(text, f"T[de]:Line {key}")

    def test_a_cue_that_was_only_partly_translated_is_not_stored(self):
        def behaviour(request):
            return Reply(body=page("no_result")) if request["q"] == "Odd" else translated(request)

        self.serve(behaviour)
        host = self.host()
        with mock.patch.object(self.module, "CHECKPOINT_EVERY", 1):
            response = self.translate(host, ["<i>Hello</i>\n<i>Odd</i>", "Plain"])
        self.assertTrue(response.ok, response.error)
        held = self.cues(self.checkpoints(host)[-1])
        self.assertEqual(held, {"1": "T[de]:Plain"})


class ConformanceTests(PluginTestCase):
    def test_the_sdk_conformance_suite_passes(self):
        self.serve()
        passed = run_conformance(
            self.module.GoogleTranslatePlugin,
            kind_api=KIND,
            samples={
                "validate": {"args": {"config": {}}},
                "migrate": {"args": {"from_version": 1, "settings": {}, "connections": []}},
                "options": {"args": {"field": "model", "config": {}}},
                "test": {"args": {"config": {}}},
                "translate": {
                    "args": {"request": {"target": {"code": "he"}, "lines": [{"position": i, "text": "Hello"} for i in range(40)]}}
                },
            },
            entry="google_translate:GoogleTranslatePlugin",
            paths=(str(PLUGIN_DIR),),
        )
        for name in ("structure", "handshake", "describe", "unknown_operation", "bad_arguments", "heartbeat", "process"):
            self.assertTrue(any(item.startswith(name) for item in passed), (name, passed))
        self.assertTrue(any(item.startswith("request translate") for item in passed))

    def test_a_translate_that_runs_long_passes_the_cancel_check(self):
        self.serve(lambda request: Reply(body=page("t0", "x"), stall=0.2))
        passed = run_conformance(
            self.module.GoogleTranslatePlugin,
            kind_api=KIND,
            samples={"translate": {"args": {"request": {"target": {"code": "de"}, "lines": ["Line %d" % i for i in range(200)]}}}},
        )
        self.assertIn("cancel", passed)


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))

    def test_it_is_a_schema_2_translator_on_the_v1_kind(self):
        self.assertEqual(self.manifest["schema_version"], 2)
        self.assertEqual(self.manifest["id"], "google_translate")
        self.assertEqual(self.manifest["kind"], "translator")
        self.assertEqual(self.manifest["kind_api"], KIND)
        self.assertEqual(self.manifest["runtime"], {"artifact": "python", "process": "worker"})

    def test_it_declares_the_network_and_both_google_origins_and_nothing_else(self):
        self.assertEqual(self.manifest["permissions"], ["network"])
        self.assertEqual(
            self.manifest["endpoints"],
            ["https://translate.google.com:443", "https://translate.googleapis.com:443"],
        )

    def test_it_has_no_settings_no_requires_and_no_parent(self):
        for key in ("settings", "settings_version", "requires", "provides", "extends", "login"):
            self.assertNotIn(key, self.manifest)

    def test_the_entry_names_the_class_in_the_file(self):
        module = load_plugin_module()
        self.assertEqual(self.manifest["entry_module"], "google_translate")
        self.assertTrue(hasattr(module, self.manifest["entry_class"]))
        self.assertTrue((PLUGIN_DIR / f"{self.manifest['entry_module']}.py").is_file())

    def test_the_library_is_pinned_exactly(self):
        lines = (PLUGIN_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("deep-translator==1.11.4", lines)
        import importlib.metadata

        self.assertEqual(importlib.metadata.version("deep-translator"), "1.11.4")

    def test_the_dependency_closure_is_locked_with_hashes(self):
        import importlib.metadata
        import re

        requirements = self.manifest["dependencies"]["requirements"]
        names = [item["name"] for item in requirements]
        self.assertEqual(len(names), len(set(names)))
        for item in requirements:
            self.assertEqual(sorted(item), ["hashes", "name", "version"])
            self.assertTrue(item["hashes"], item["name"])
            for digest in item["hashes"]:
                self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        locked = {item["name"]: item["version"] for item in requirements}
        self.assertEqual(locked["deep-translator"], "1.11.4")
        wanted = {
            re.split(r"[\s;<>=!~(\[]", line, maxsplit=1)[0].lower().replace("_", "-")
            for line in importlib.metadata.requires("deep-translator")
            if "extra ==" not in line
        }
        self.assertLessEqual(wanted, {name.lower() for name in names})

    def test_requests_and_urllib3_are_pinned_to_ranges_the_lock_satisfies(self):
        import re

        lines = [
            line for line in (PLUGIN_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        locked = {item["name"]: item["version"] for item in self.manifest["dependencies"]["requirements"]}

        def numbers(version):
            return tuple(int(part) for part in version.split("."))

        for name in ("requests", "urllib3"):
            line = next(line for line in lines if line.startswith(name))
            low, high = re.fullmatch(rf"{name}>=([\d.]+),<([\d.]+)", line).groups()
            self.assertLessEqual(numbers(low), numbers(locked[name]))
            self.assertLess(numbers(locked[name]), numbers(high))
        self.assertIn("deep-translator==1.11.4", lines)

    def test_the_plugin_code_uses_only_the_first_origin(self):
        source = (PLUGIN_DIR / "google_translate.py").read_text(encoding="utf-8")
        module = load_plugin_module()
        self.assertEqual(module.PAGE_URL, "https://translate.google.com/m")
        self.assertNotIn("googleapis", source)

    def test_the_second_origin_is_declared_but_reserved_and_says_so(self):
        self.assertIn("https://translate.googleapis.com:443", self.manifest["endpoints"])
        readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("translate.googleapis.com", readme)
        self.assertIn("reserved", readme)

    def test_the_module_documents_what_a_retry_must_hand_back(self):
        doc = load_plugin_module().__doc__
        for phrase in ('request["checkpoints"]', 'request["checkpoint"]', "cumulative", "cues_z"):
            self.assertIn(phrase, doc)


if __name__ == "__main__":
    unittest.main()
