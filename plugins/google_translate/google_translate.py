"""Google Translate, as a Bazarr+ translator plugin (``bazarr.translator.v1``).

No key and no settings. The plugin reads the answer out of Google's public mobile
translation page through the pinned ``deep-translator`` library, which is what Bazarr+
has always done for its free engine. Three things differ from calling the library
directly:

* every request has a ceiling (the library sets none), and a timeout counts as a
  failed request that is retried;
* target languages are mapped to the library's codes, and a language Google lacks is
  declined, so Bazarr+ can hand the job to another engine;
* the subtitle tags ``<i>``, ``<b>``, ``<u>`` and ``<s>`` are taken off before a cue is
  sent and put back after.

Failures use the shared vocabulary: a language Google lacks or no route to Google is
``declined`` (``language_unsupported`` or ``server_unreachable``), a throttle or a block
page is ``rate_limited`` with a conservative retry time, and nothing raw leaves the
plugin.

The plugin runs on the SDK's blocking-library support: one asyncio task per job, with
the requests themselves on one thread pool that every job of the worker shares, so the
worker never has more than ``CONCURRENCY`` requests in flight.
"""

from __future__ import annotations

import asyncio
import collections
import random
import re
import socket
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import requests

from bazarr_plugin_sdk import PluginContext, PluginError
from bazarr_plugin_sdk.translator.v1 import TranslatorPlugin
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from deep_translator.constants import BASE_URLS, GOOGLE_LANGUAGES_TO_CODES
from deep_translator.exceptions import NotValidLength, NotValidPayload, TranslationNotFound

# The page the library reads. Tests point this at a local server.
PAGE_URL = BASE_URLS["GOOGLE_TRANSLATE"]

# The ceiling on one request, in seconds, from the first byte sent to the last byte read.
REQUEST_TIMEOUT = 30.0
# Requests in flight at once, for the whole worker and every job it runs.
CONCURRENCY = 10
# Attempts for one request (one try and five retries, the policy Bazarr+ has always used),
# with exponential backoff and jitter between them.
MAX_TRIES = 6
BACKOFF_START = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_JITTER = 1.0
# A checkpoint is stored for every this many completed cues.
CHECKPOINT_EVERY = 100
# What a throttled job tells Bazarr+ to wait, in seconds, before it tries Google again.
THROTTLE_RETRY_AFTER = 900.0
# A page larger than this is not a translation answer.
MAX_PAGE_BYTES = 2 * 1024 * 1024

MODEL = "google_translate"
TEST_PHRASE = "Hello, world."
TEST_TARGET = "de"

# --------------------------------------------------------------------------- languages

# The codes the pinned library lists, read once at start.
LIBRARY_CODES = frozenset(GOOGLE_LANGUAGES_TO_CODES.values())

# Bazarr+ sends Hebrew as he and Javanese as jv, which the library still spells with the
# retired codes iw and jw. Norwegian Bokmal goes to the library's plain no.
_LANGUAGE_ALIASES = {"he": "iw", "jv": "jw", "nb": "no"}
_SUBTAG = re.compile(r"^[A-Za-z]{2,3}$")
_TRADITIONAL_REGIONS = frozenset({"TW", "HK", "MO"})


def library_code(tag: Any) -> str | None:
    """The library's code for a BCP-47 target tag, or None when Google does not offer it.

    A tag the library lists is used as it is. A regional variant the library lists only by
    its base language (pt-BR, es-419) goes to the base. Chinese is the one language the
    library lists only with a script's default region: Traditional (the script Hant, or
    the regions TW, HK and MO) goes to zh-TW, anything else to zh-CN.
    """
    if not isinstance(tag, str):
        return None
    parts = tag.strip().replace("_", "-").split("-")
    if not _SUBTAG.match(parts[0]):
        return None
    language = parts[0].lower()
    script = region = None
    for part in parts[1:]:
        if script is None and region is None and len(part) == 4 and part.isalpha():
            script = part.title()
        elif region is None and ((len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit())):
            region = part.upper()
        else:
            return None
    if language == "zh":
        traditional = script == "Hant" or (script is None and region in _TRADITIONAL_REGIONS)
        code = "zh-TW" if traditional else "zh-CN"
        return code if code in LIBRARY_CODES else None
    language = _LANGUAGE_ALIASES.get(language, language)
    candidates = []
    if script and region:
        candidates.append(f"{language}-{script}-{region}")
    if script:
        candidates.append(f"{language}-{script}")
    if region:
        candidates.append(f"{language}-{region}")
    candidates.append(language)
    return next((code for code in candidates if code in LIBRARY_CODES), None)


def supported_targets() -> list[str]:
    """The BCP-47 target tags this plugin accepts, for ``describe``."""
    spelled = {code: tag for tag, code in _LANGUAGE_ALIASES.items()}
    tags = {spelled.get(code, code) for code in LIBRARY_CODES}
    if {"zh-CN", "zh-TW"} <= LIBRARY_CODES:
        tags.update({"zh", "zh-Hans", "zh-Hant"})
    return sorted(tags, key=str.lower)


# --------------------------------------------------------------------------- tags

# A subtitle tag Bazarr+ passes, or an ASS override block such as {\an8}.
_MARKUP = r"</?[ibus]>|\{\\[^{}]*\}"
_MARKUP_RE = re.compile(_MARKUP, re.IGNORECASE)
_PEEL_RE = re.compile(rf"^(?P<pre>(?:{_MARKUP})*)(?P<body>.*?)(?P<suf>(?:{_MARKUP})*)$", re.IGNORECASE | re.DOTALL)

_Part = tuple[str, str, str]  # (markup before, text, markup after)


def _peel(text: str) -> _Part:
    match = _PEEL_RE.match(text)
    return match["pre"], match["body"], match["suf"]  # always matches: every part may be empty


def _plan(text: str) -> tuple[list[_Part], str, bool]:
    """Split a cue into parts to translate, how the parts join, and whether tags were lost.

    Tags that wrap the whole cue, or each line of it, are taken off and put back around
    the translation. Tags in the middle of a line cannot be put back where they belong,
    so the cue is translated without them.
    """
    pre, body, suf = _peel(text)
    if not _MARKUP_RE.search(body):
        return [(pre, body, suf)], "", False
    lines = [_peel(line) for line in text.split("\n")]
    if not any(_MARKUP_RE.search(body) for _pre, body, _suf in lines):
        return lines, "\n", False
    return [("", _MARKUP_RE.sub("", text), "")], "", True


def _translatable(text: str) -> bool:
    """Whether the text has anything to translate once its tags are off."""
    return any(char.isalnum() for char in _MARKUP_RE.sub("", text))


# --------------------------------------------------------------------------- requests


class _Throttled(Exception):
    """Google answered 429 or showed a block page."""


class _Unreachable(Exception):
    """No usable answer from Google: a timeout, a refused or failed connection, or an HTTP error."""


class _Stopped(Exception):
    """The job was cancelled or aborted; the request was not sent or not retried."""


class _Unexpected(Exception):
    """Anything else, carried across the thread boundary without its raw message."""


def _looks_blocked(page: str) -> bool:
    lowered = page.lower()
    return "unusual traffic" in lowered or "captcha" in lowered


class _Page(GoogleTranslator):
    """The library's translator with a ceiling on its request and a classified failure."""

    def __init__(self, target: str):
        super().__init__(source="auto", target=target)
        self._base_url = PAGE_URL

    def translate_bounded(self, text: str) -> str | None:
        """What ``GoogleTranslator.translate`` returns, from one request that cannot outlast the ceiling.

        None when Google echoes text that has no letters or digits in it, as the library returns.
        Raises ``TranslationNotFound`` when the page holds no answer, ``_Throttled`` or
        ``_Unreachable`` when the request fails.
        """
        text = text.strip()
        if not text:
            return text
        if len(text) >= 5000:  # the library's own limit
            raise NotValidLength(text, 0, 5000)
        params = {"tl": self._target, "sl": self._source, self.payload_key: text}
        page = _fetch(self._base_url, params)
        soup = BeautifulSoup(page, "html.parser")
        element = soup.find(self._element_tag, self._element_query) or soup.find(self._element_tag, self._alt_element_query)
        if not element:
            if _looks_blocked(page):
                raise _Throttled()
            raise TranslationNotFound(text)
        answer = element.get_text(strip=True)
        if answer != text:
            return answer
        sent = "".join(char for char in text if char.isalnum())
        got = "".join(char for char in answer if char.isalnum())
        return text if sent and got and sent == got else None


def _socket_of(response: requests.Response) -> socket.socket | None:
    """The socket under a streamed response: urllib3 keeps it on the connection, or, when the
    server closes after its answer, only on the file the answer is read from."""
    raw = response.raw
    sock = getattr(getattr(raw, "_connection", None), "sock", None)
    if sock is None:
        sock = getattr(getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None), "_sock", None)
    return sock


def _abort_at(response: requests.Response, seconds: float) -> threading.Timer:
    """Shut the response's socket down after ``seconds``, so a server that trickles its answer
    cannot outlast the ceiling: a read that is blocked then fails at once."""

    def abort() -> None:
        try:
            sock = _socket_of(response)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # the answer is already read and the socket is closed
            pass

    timer = threading.Timer(max(seconds, 0.0), abort)
    timer.daemon = True
    timer.start()
    return timer


def _fetch(url: str, params: dict[str, str]) -> str:
    """One GET, bounded as a whole by ``REQUEST_TIMEOUT``. Returns the page text or raises."""
    limit = REQUEST_TIMEOUT
    started = time.monotonic()
    try:
        # The connect and the wait for the first answer together take at most the ceiling.
        response = requests.get(
            url, params=params, timeout=(limit / 3, limit - limit / 3), allow_redirects=False, stream=True
        )
    except requests.RequestException:
        raise _Unreachable() from None
    watchdog = _abort_at(response, limit - (time.monotonic() - started))
    try:
        status = response.status_code
        if status == 429:
            raise _Throttled()
        if 300 <= status < 400:
            # A redirect to Google's "sorry" page is the block page; any other redirect leaves the
            # origins the plugin declared, so it is not followed.
            raise _Throttled() if "/sorry/" in response.headers.get("Location", "") else _Unreachable()
        if not 200 <= status < 300:
            raise _Unreachable()
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_content(8192):
                size += len(chunk)
                if size > MAX_PAGE_BYTES or time.monotonic() - started > limit:
                    raise _Unreachable()
                chunks.append(chunk)
        except _Unreachable:
            raise
        except Exception:  # a read that failed, timed out or was cut off by the watchdog
            raise _Unreachable() from None
        if time.monotonic() - started > limit:
            raise _Unreachable()
        content_type = response.headers.get("Content-Type", "").lower()
        encoding = response.encoding if "charset" in content_type and response.encoding else "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")
    finally:
        watchdog.cancel()
        response.close()


def _wait(seconds: float, stop: Callable[[], bool]) -> None:
    deadline = time.monotonic() + seconds
    while True:
        if stop():
            raise _Stopped()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _translate_text(text: str, target: str, stop: Callable[[], bool], tries: int | None = None) -> str | None:
    """Translate one piece of text, retrying a throttle or a failed request with backoff.

    Cancel is checked before every request and during every wait. Returns None when Google
    has no answer for the text. Raises ``_Throttled`` or ``_Unreachable`` once the tries run out.
    """
    tries = MAX_TRIES if tries is None else tries
    delay = BACKOFF_START
    attempt = 0
    while True:
        attempt += 1
        if stop():
            raise _Stopped()
        try:
            return _Page(target).translate_bounded(text)
        except (_Throttled, _Unreachable):
            if attempt >= tries:
                raise
            _wait(delay + random.uniform(0, BACKOFF_JITTER), stop)
            delay *= BACKOFF_FACTOR
        except (NotValidLength, NotValidPayload, TranslationNotFound):
            return None
        except Exception as error:
            raise _Unexpected(error.__class__.__name__) from None


class _Cue:
    """What one cue came to: its text and how it went."""

    __slots__ = ("text", "translated", "empty", "tags_lost")

    def __init__(self, text: str, *, translated: bool = False, empty: bool = False, tags_lost: bool = False):
        self.text = text
        self.translated = translated  # some Google text is in `text`
        self.empty = empty  # the cue had text to translate and Google gave none
        self.tags_lost = tags_lost


def _translate_cue(text: str, target: str, stop: Callable[[], bool]) -> _Cue:
    parts, joiner, lost = _plan(text)
    out = []
    got = missing = 0
    for pre, body, suf in parts:
        inner = body.strip()
        if not _translatable(inner):
            out.append(pre + body + suf)
            continue
        answer = _translate_text(inner, target, stop)
        if answer is None or not answer.strip():
            out.append(pre + body + suf)
            missing += 1
        else:
            out.append(pre + answer + suf)
            got += 1
    result = joiner.join(out)
    if lost and not got:
        result = text  # nothing was translated, so there is nothing to strip
    return _Cue(result, translated=bool(got), empty=bool(missing and not got), tags_lost=lost and bool(got))


_executor_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    """The thread pool every job of this worker shares."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="google-translate")
        return _executor


# --------------------------------------------------------------------------- the plugin


def _declined(message: str, reason: str) -> PluginError:
    return PluginError("declined", message, reason=reason)


def _failure(error: Exception) -> PluginError:
    if isinstance(error, _Throttled):
        return PluginError(
            "rate_limited",
            "Google is limiting translation requests from this address. The job retries later.",
            retry_after=THROTTLE_RETRY_AFTER,
        )
    if isinstance(error, _Unreachable):
        return _declined("Google Translate could not be reached.", "server_unreachable")
    name = str(error) if isinstance(error, _Unexpected) else error.__class__.__name__
    return PluginError("internal", f"Google Translate failed unexpectedly ({name}).")


def _language_code(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("code")
    return value if isinstance(value, str) else None


def _line_text(line: Any) -> str:
    text = line.get("text") if isinstance(line, dict) else line
    return text if isinstance(text, str) else ""


def _with_text(line: Any, text: str) -> Any:
    return {**line, "text": text} if isinstance(line, dict) else text


def _resumed(request: dict[str, Any], count: int) -> dict[int, str]:
    """The cues an earlier attempt finished, from the request's checkpoints."""
    found: dict[int, str] = {}
    saved = request.get("checkpoints")
    if saved is None and request.get("checkpoint") is not None:
        saved = [request["checkpoint"]]
    for item in saved if isinstance(saved, list) else []:
        data = item.get("data", item) if isinstance(item, dict) else None
        cues = data.get("cues") if isinstance(data, dict) else None
        if not isinstance(cues, dict):
            continue
        for key, text in cues.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= index < count and isinstance(text, str):
                found[index] = text
    return found


class GoogleTranslatePlugin(TranslatorPlugin):
    """The ``google_translate`` plugin."""

    # Jobs at once. The requests of all of them share the one pool of CONCURRENCY threads.
    max_in_flight = 4

    def describe(self) -> dict[str, Any]:
        return {
            "backend_types": [],
            "source_languages": ["auto"],
            "target_languages": supported_targets(),
            "auto_detect": True,
            "limits": {"concurrent_requests": CONCURRENCY, "request_timeout_seconds": REQUEST_TIMEOUT},
        }

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise PluginError("invalid_config", "The configuration must be an object.")
        if config:
            names = sorted(str(key) for key in config)
            raise PluginError(
                "invalid_config",
                f"Google Translate has no settings: {', '.join(names)}.",
                field_errors={name: "Google Translate has no such setting." for name in names},
            )
        return {}

    def migrate(self, from_version: int, settings: dict[str, Any], connections: list[dict[str, Any]]) -> dict[str, Any]:
        if from_version != 1:
            raise PluginError("invalid_config", f"There is no migration from settings version {from_version}.")
        return {"settings": settings, "connections": connections}

    async def options(
        self, field: str, config: dict[str, Any], secrets: dict[str, Any], ctx: PluginContext | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def test(
        self, config: dict[str, Any], secrets: dict[str, Any], ctx: PluginContext | None = None
    ) -> dict[str, Any]:
        """One real request for a short fixed phrase. It costs nothing, so it reserves nothing."""
        self.validate(config)

        def stop() -> bool:
            return bool(ctx is not None and ctx.is_cancelled)

        loop = asyncio.get_running_loop()
        try:
            answer = await loop.run_in_executor(_pool(), _translate_text, TEST_PHRASE, TEST_TARGET, stop, 1)
        except _Stopped:
            raise PluginError("cancelled", "The test was cancelled.") from None
        except (_Throttled, _Unreachable, _Unexpected) as error:
            failure = _failure(error)
            if failure.code == "declined":
                failure = PluginError("dependency_unavailable", "Google Translate could not be reached.")
            raise failure from None
        if not answer or not answer.strip():
            raise PluginError("internal", "Google returned no usable text.")
        return {"ok": True, "message": "Google Translate answered."}

    async def translate(self, request: dict[str, Any], ctx: PluginContext) -> dict[str, Any]:
        target = library_code(_language_code(request.get("target")))
        if target is None:
            raise _declined(
                f"Google Translate does not offer {_language_code(request.get('target')) or 'that language'}.",
                "language_unsupported",
            )
        lines = request.get("lines")
        if not isinstance(lines, list):
            raise PluginError("internal", "The request has no lines to translate.")
        total = len(lines)
        texts = [_line_text(line) for line in lines]
        outputs = list(texts)
        done = _resumed(request, total)
        for index, text in done.items():
            outputs[index] = text
        pending = collections.deque(index for index in range(total) if index not in done and _translatable(texts[index]))
        # Cues with nothing to translate (blank, notes, digits only) go back as they came.
        finished = total - len(pending)
        usable = len(done)
        empty = tags_lost = 0
        unsaved: dict[str, str] = {}

        stop_flag = threading.Event()

        def stop() -> bool:
            return stop_flag.is_set() or ctx.is_cancelled

        beat = max(0.05, min(1.0, float(ctx.heartbeat_interval or 1.0) / 2))
        loop = asyncio.get_running_loop()
        running: dict[asyncio.Future, int] = {}
        failure: Exception | None = None
        ctx.progress(finished, total, f"translated {finished} of {total} cues")

        try:
            while (pending or running) and failure is None:
                while pending and len(running) < CONCURRENCY:
                    index = pending.popleft()
                    running[loop.run_in_executor(_pool(), _translate_cue, texts[index], target, stop)] = index
                ready, _ = await asyncio.wait(running, timeout=beat, return_when=asyncio.FIRST_COMPLETED)
                ctx.heartbeat()
                for future in sorted(ready, key=running.__getitem__):
                    index = running.pop(future)
                    try:
                        cue = future.result()
                    except (_Throttled, _Unreachable, _Unexpected) as error:
                        failure = failure or error
                        continue
                    except _Stopped:
                        ctx.raise_if_cancelled()
                        continue
                    outputs[index] = cue.text
                    finished += 1
                    empty += cue.empty
                    tags_lost += cue.tags_lost
                    if cue.translated:
                        usable += 1
                        unsaved[str(index)] = cue.text
                    if len(unsaved) >= CHECKPOINT_EVERY:
                        await ctx.checkpoint({"v": 1, "cues": unsaved})
                        unsaved = {}
                ctx.progress(finished, total, f"translated {finished} of {total} cues")
        finally:
            stop_flag.set()
            for future in running:
                future.cancel()

        if failure is not None:
            if unsaved and not ctx.is_cancelled:
                try:
                    await ctx.checkpoint({"v": 1, "cues": unsaved})
                except PluginError:
                    pass
            raise _failure(failure)
        ctx.raise_if_cancelled()
        if usable == 0 and any(_translatable(text) for text in texts):
            raise PluginError("internal", "Google returned no usable text.")

        notes = [f"Translated {total} cues with Google Translate."]
        if tags_lost:
            notes.append(f"{tags_lost} cues came back without their tags.")
        if empty:
            notes.append(f"{empty} cues kept their source text because Google gave no answer for them.")
        return {
            "status": "complete",
            "lines": [_with_text(line, text) for line, text in zip(lines, outputs)],
            "message": " ".join(notes),
            "tokens": 0,
            "cost": 0.0,
            "models": [MODEL],
        }
