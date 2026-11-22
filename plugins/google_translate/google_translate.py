"""Google Translate, as a Bazarr+ translator plugin (``bazarr.translator.v1``).

No key and no settings. The plugin reads the answer out of Google's public mobile
translation page through the pinned ``deep-translator`` library, which is what Bazarr+
has always done for its free engine. Three things differ from calling the library
directly:

* every request has a ceiling (the library sets none), and a timeout counts as a
  failed request that is retried;
* target languages are mapped to the library's codes, and a language Google lacks is
  declined, so Bazarr+ can hand the job to another engine;
* the subtitle tags (``<i>``, ``<b>``, ``<u>``, ``<s>``, and any other such as ``<font ...>``) are taken off before a cue is
  sent and put back after.

Failures use the shared vocabulary: a language Google lacks or no route to Google is
``declined`` (``language_unsupported`` or ``server_unreachable``), a throttle or a block
page is ``rate_limited`` with a conservative retry time, and nothing raw leaves the
plugin.

The plugin runs on the SDK's blocking-library support: one asyncio task per job, with
the requests themselves on one thread pool that every job of the worker shares, so the
worker never has more than ``CONCURRENCY`` requests in flight. A request that is abandoned at
its deadline keeps its slot until its own thread ends.

Checkpoints and resume
----------------------
A checkpoint holds only what the job finished since the previous one was stored:
``{"v": 3, "target": <code>, "digest": <hash>, "cues_z": <text>}``. ``target`` is the
library code the job translates into and ``digest`` is a SHA-256 of the request's source
texts, so a checkpoint made for another target or another version of the file is ignored.
``cues_z`` is a base64 string of the zlib-compressed JSON object that maps a cue's index in
the request's ``lines`` (as a string) to its final text. One is stored for every
``CHECKPOINT_EVERY`` newly completed cues, and one more when a job fails. A cue that came back
only in part, or with no answer, is never stored, so a retry tries it again. A checkpoint the host
did not accept keeps its cues for the next one.

The host stores every checkpoint as a batch of its own and hands all of them back in the request of a
retry, which a control frame caps at 1 MiB. Because each checkpoint is a delta, what is stored in
all grows with the text of the file and not with the square of its cue count. The plugin also
measures the request (its source lines and the checkpoints it received) and stops storing once the
next checkpoint would push a retry request past ``REQUEST_BUDGET_BYTES``; the cues after that are
translated again on a retry.

On a retry the plugin expects the host to hand back what it stored in the request: either
``request["checkpoints"]``, a list of the ``data`` objects stored, oldest first, or
``request["checkpoint"]``, one of them. Each item may also be wrapped as ``{"data": ...}``.
The plugin merges every checkpoint it is given, a later one winning. Cues it is given are returned as
stored and are not sent to Google again. A checkpoint of another version, or one it cannot read, is
ignored.

Result
------
The host reads ``status``, ``lines``, ``missing`` and ``model``. A cue Google gave no text for, or
only some of its text, is not returned as a translation: its position goes in ``missing``, it has no
entry in ``lines``, and the status is ``partial``. The host then keeps the source text for it and
records the job as partial. The status is ``complete`` only when every cue that has text to
translate came back translated.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import hashlib
import json
import random
import re
import socket
import threading
import time
import zlib

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
CHECKPOINT_VERSION = 3
# The host hands every checkpoint of a job back in the request of a retry, and a request frame is capped at
# 1 MiB. The plugin keeps the request, its source lines included, under this many bytes, and stops storing
# checkpoints when the next one would not fit.
REQUEST_BUDGET_BYTES = 900 * 1024
CHECKPOINT_OVERHEAD_BYTES = 256
# Once this many cues are done and every one was refused with an HTTP 4xx and none got text, the job is declined.
REFUSAL_PROBE = 20
# What a throttled job tells Bazarr+ to wait, in seconds, before it tries Google again: Google's own
# Retry-After when it sends one, kept between the two bounds, else the default.
THROTTLE_RETRY_AFTER = 900.0
THROTTLE_RETRY_MIN = 60.0
THROTTLE_RETRY_MAX = 3600.0
# Requests abandoned at the deadline that may still be alive at once. Past this, new requests are refused.
MAX_ABANDONED = 5
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
    """The BCP-47 target tags this plugin accepts, for ``describe``.

    A host that filters on this list must not lose a language the plugin takes: Hebrew is listed
    as he (not the library's iw), Javanese as jv (not jw), and Norwegian as both no and nb.
    """
    retired = {code for code in _LANGUAGE_ALIASES.values() if code in ("iw", "jw")}
    tags = {code for code in LIBRARY_CODES if code not in retired}
    tags.update(alias for alias, code in _LANGUAGE_ALIASES.items() if code in LIBRARY_CODES)
    if {"zh-CN", "zh-TW"} <= LIBRARY_CODES:
        tags.update({"zh", "zh-Hans", "zh-Hant"})
    return sorted(tags, key=str.lower)


# --------------------------------------------------------------------------- tags

# A tag (<i>, <b>, <u>, <s> and anything like <font color="#fff">) or an ASS override block such as {\an8}.
_MARKUP = r"</?[A-Za-z][^<>]*>|\{\\[^{}]*\}"
_MARKUP_RE = re.compile(_MARKUP, re.IGNORECASE)
_OVERRIDE_RE = re.compile(r"\{\\[^{}]*\}")
_PEEL_RE = re.compile(rf"\A(?P<pre>(?:{_MARKUP})*)(?P<body>.*?)(?P<suf>(?:{_MARKUP})*)\Z", re.IGNORECASE | re.DOTALL)

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
    # The override blocks in front (positioning, such as {\an8}) still apply to the translation.
    leading = "".join(_OVERRIDE_RE.findall(pre))
    return [(leading, _MARKUP_RE.sub("", text), "")], "", True


def _translatable(text: str) -> bool:
    """Whether the text has anything to translate once its tags are off."""
    return any(char.isalnum() for char in _MARKUP_RE.sub("", text))


# --------------------------------------------------------------------------- requests


class _Throttled(Exception):
    """Google answered 429 or showed a block page. ``retry_after`` is Google's Retry-After in seconds, if it sent one."""

    def __init__(self, retry_after: float | None = None):
        super().__init__()
        self.retry_after = retry_after


class _NoText(Exception):
    """The first cues of a job all came back with no text and none was translated."""


class _Unreachable(Exception):
    """No usable answer from Google: a timeout, a refused or failed connection, or an HTTP error."""


class _Refused(Exception):
    """Google refused cue after cue with an HTTP 4xx and gave no text for any."""


class _NoAnswer(Exception):
    """Google refused this one text (an HTTP 4xx other than 403 or 429): the cue is skipped."""


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

    def translate_bounded(self, text: str, stop: Callable[[], bool]) -> str | None:
        """What ``GoogleTranslator.translate`` returns, from one request that cannot outlast the ceiling.

        None when Google echoes text that has no letters or digits in it, as the library returns.
        Raises ``TranslationNotFound`` when the page holds no answer, ``_NoAnswer`` when Google
        refuses the text, ``_Throttled`` or ``_Unreachable`` when the request fails, and
        ``_Stopped`` when ``stop`` says so.
        """
        text = text.strip()
        if not text:
            return text
        if len(text) >= 5000:  # the library's own limit
            raise NotValidLength(text, 0, 5000)
        params = {"tl": self._target, "sl": self._source, self.payload_key: text}
        page = _fetch(self._base_url, params, stop)
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


# Requests in flight, abandoned ones included: a slot is held until the request's own thread ends.
_slots = threading.BoundedSemaphore(CONCURRENCY)
_abandon_lock = threading.Lock()
_abandoned_alive = 0


def _fetch(url: str, params: dict[str, str], stop: Callable[[], bool]) -> str:
    """One GET under a single deadline, ``REQUEST_TIMEOUT``, for the lookup, connect, headers and body.

    The request runs on a thread of its own, which holds one of ``CONCURRENCY`` slots until it
    really ends. The caller waits for the request to finish, for ``stop`` or for the deadline,
    whichever comes first, and abandons the request on the last two: a slow name lookup, a server
    that trickles its headers or one that goes quiet cannot hold the caller past the deadline.
    An abandoned request closes itself as soon as it returns, and the per-read timeouts inside it
    end it when the server stalls. At most ``MAX_ABANDONED`` of them live at once; beyond that
    new requests are refused as unreachable instead of starting more threads. A request the caller
    gave up on because of a cancel is not counted.
    """
    global _abandoned_alive
    with _abandon_lock:
        if _abandoned_alive >= MAX_ABANDONED:
            raise _Unreachable()
    slots = _slots
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while not slots.acquire(timeout=0.1):
        if stop():
            raise _Stopped()
        if time.monotonic() >= deadline:
            raise _Unreachable()
    outcome: dict[str, Any] = {}
    finished = threading.Event()
    abandoned = threading.Event()
    counted = []  # holds one item once the request counts against MAX_ABANDONED

    def run() -> None:
        global _abandoned_alive
        try:
            outcome["page"] = _fetch_page(url, params, abandoned)
        except BaseException as error:  # handed to the caller below
            outcome["error"] = error
        finally:
            with _abandon_lock:
                finished.set()
                if counted:
                    _abandoned_alive -= 1
            slots.release()

    try:
        threading.Thread(target=run, name="google-translate-request", daemon=True).start()
    except BaseException:
        slots.release()
        raise _Unreachable() from None
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while not finished.wait(0.1):
        cancelled = stop()
        if cancelled or time.monotonic() >= deadline:
            # One lock covers the abandon, its count and the cap check of every new request, so the
            # count a request sees is never one a concurrent abandon is about to change. A cancel
            # leaves the request to close itself but is the user's doing, so it is not counted.
            with _abandon_lock:
                if not finished.is_set():
                    abandoned.set()
                    if cancelled:
                        raise _Stopped()
                    counted.append(True)
                    _abandoned_alive += 1
                    raise _Unreachable()
            break  # it finished while the lock was being taken
    if "error" in outcome:
        raise outcome["error"]
    return outcome["page"]


def _fetch_page(url: str, params: dict[str, str], abandoned: threading.Event) -> str:
    limit = REQUEST_TIMEOUT
    started = time.monotonic()
    try:
        # The connect and the wait for the first answer are bounded per read as well.
        response = requests.get(
            url, params=params, timeout=(limit / 3, limit - limit / 3), allow_redirects=False, stream=True
        )
    except requests.RequestException:
        raise _Unreachable() from None
    watchdog = _abort_at(response, limit - (time.monotonic() - started))
    try:
        if abandoned.is_set():
            raise _Unreachable()
        status = response.status_code
        if status == 429:
            raise _Throttled(_retry_after(response.headers.get("Retry-After")))
        if 300 <= status < 400:
            # A redirect to Google's "sorry" page is the block page; any other redirect leaves the
            # origins the plugin declared, so it is not followed.
            raise _Throttled() if "/sorry/" in response.headers.get("Location", "") else _Unreachable()
        if 400 <= status < 500 and status != 403:
            raise _NoAnswer()
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


def _retry_after(value: str | None) -> float | None:
    """A Retry-After given in seconds, or None. The HTTP-date form is not used."""
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds == seconds and 0 <= seconds < 10**7 else None


def _wait(seconds: float, stop: Callable[[], bool]) -> None:
    deadline = time.monotonic() + seconds
    while True:
        if stop():
            raise _Stopped()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _translate_text(
    text: str,
    target: str,
    stop: Callable[[], bool],
    tries: int | None = None,
    throttled: threading.Event | None = None,
) -> str | None:
    """Translate one piece of text, retrying a throttle or a failed request with backoff.

    ``throttled`` is shared by the cues of one job. The second throttle in a row on one cue sets it,
    and once it is set a throttled request is not retried, so a block that is already plain does not
    cost every cue in flight all its tries.

    Cancel is checked before every request, during every request and during every wait. Returns
    None when Google has no answer for the text. Raises ``_NoAnswer`` when Google refuses it,
    and ``_Throttled`` or ``_Unreachable`` once the tries run out.
    """
    tries = MAX_TRIES if tries is None else tries
    delay = BACKOFF_START
    attempt = 0
    while True:
        attempt += 1
        if stop():
            raise _Stopped()
        try:
            return _Page(target).translate_bounded(text, stop)
        except (_Throttled, _Unreachable) as error:
            if isinstance(error, _Throttled) and throttled is not None:
                if throttled.is_set():
                    raise
                if attempt >= 2:
                    throttled.set()
                    raise
            if attempt >= tries:
                raise
            _wait(delay + random.uniform(0, BACKOFF_JITTER), stop)
            delay *= BACKOFF_FACTOR
        except (NotValidLength, NotValidPayload, TranslationNotFound):
            return None
        except (_NoAnswer, _Stopped):
            raise
        except Exception as error:
            raise _Unexpected(error.__class__.__name__) from None


class _Cue:
    """What one cue came to: its text and how it went."""

    __slots__ = ("text", "translated", "empty", "refused", "partial", "tags_lost")

    def __init__(
        self,
        text: str,
        *,
        translated: bool = False,
        empty: bool = False,
        refused: bool = False,
        partial: bool = False,
        tags_lost: bool = False,
    ):
        self.text = text
        self.translated = translated  # some Google text is in `text`
        self.empty = empty  # the cue had text to translate and Google gave none
        self.refused = refused  # ... and every part of it that got none was refused with an HTTP 4xx
        self.partial = partial  # some of its parts have Google text and some kept the source
        self.tags_lost = tags_lost


def _translate_cue(
    text: str, target: str, stop: Callable[[], bool], throttled: threading.Event | None = None
) -> _Cue:
    parts, joiner, lost = _plan(text)
    out = []
    got = missing = refusals = 0
    for pre, body, suf in parts:
        inner = body.strip()
        if not _translatable(inner):
            out.append(pre + body + suf)
            continue
        # Whitespace around the text, a trailing newline for one, stays where it was.
        lead, trail = body[: len(body) - len(body.lstrip())], body[len(body.rstrip()) :]
        try:
            answer = _translate_text(inner, target, stop, None, throttled)
        except _NoAnswer:
            answer = None
            refusals += 1
        if answer is None or not answer.strip():
            out.append(pre + body + suf)
            missing += 1
        else:
            out.append(pre + lead + answer + trail + suf)
            got += 1
    result = joiner.join(out)
    if lost and not got:
        result = text  # nothing was translated, so there is nothing to strip
    return _Cue(
        result,
        translated=bool(got),
        empty=bool(missing and not got),
        refused=bool(missing and refusals == missing and not got),
        partial=bool(missing and got),
        tags_lost=lost and bool(got),
    )


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
        wait = error.retry_after
        wait = THROTTLE_RETRY_AFTER if wait is None else min(THROTTLE_RETRY_MAX, max(THROTTLE_RETRY_MIN, wait))
        return PluginError(
            "rate_limited",
            "Google is limiting translation requests from this address. The job retries later.",
            retry_after=wait,
        )
    if isinstance(error, _NoText):
        return PluginError("internal", "Google returned no usable text.")
    if isinstance(error, _Unreachable):
        return _declined("Google Translate could not be reached.", "server_unreachable")
    if isinstance(error, _Refused):
        return _declined("Google Translate refused every request.", "server_unreachable")
    name = str(error) if isinstance(error, _Unexpected) else error.__class__.__name__
    return PluginError("internal", f"Google Translate failed unexpectedly ({name}).")


_HOST_CONFIG_KEYS = frozenset({"settings", "connection", "limits"})


def _own_settings(config: dict[str, Any]) -> dict[str, Any]:
    """The plugin's own settings out of a configuration in either shape.

    A bare object is the settings themselves. The host sends ``{"settings", "connection", "limits"}``,
    and the settings are inside it. This plugin has no settings, so either way they must be empty.
    """
    if config and set(config) <= _HOST_CONFIG_KEYS:
        settings = config.get("settings")
        if settings is None:
            return {}
        if not isinstance(settings, dict):
            raise PluginError("invalid_config", "The settings must be an object.")
        return settings
    return config


def _language_code(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("code")
    return value if isinstance(value, str) else None


def _line_text(line: Any) -> str:
    text = line.get("text") if isinstance(line, dict) else line
    return text if isinstance(text, str) else ""


def _position(line: Any, index: int) -> int:
    value = line.get("position") if isinstance(line, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else index


def _with_text(line: Any, text: str, index: int) -> dict[str, Any]:
    """A result line: the request's line with the new text, or one that carries its position."""
    return {**line, "text": text} if isinstance(line, dict) else {"position": index, "text": text}


def source_digest(texts: list[str]) -> str:
    """A hash of the request's source texts, which ties a checkpoint to the file it was made from."""
    return hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()


def pack_cues(cues: dict[int, str], target: str, digest: str) -> dict[str, Any]:
    """The checkpoint data for a set of finished cues (see the module docstring)."""
    blob = zlib.compress(json.dumps({str(index): text for index, text in cues.items()}).encode("utf-8"))
    return {"v": CHECKPOINT_VERSION, "target": target, "digest": digest, "cues_z": base64.b64encode(blob).decode("ascii")}


def _unpack_cues(data: Any, target: str, digest: str) -> dict[str, str]:
    if (
        not isinstance(data, dict)
        or data.get("v") != CHECKPOINT_VERSION
        or data.get("target") != target
        or data.get("digest") != digest
        or not isinstance(data.get("cues_z"), str)
    ):
        return {}
    try:
        cues = json.loads(zlib.decompress(base64.b64decode(data["cues_z"], validate=True)).decode("utf-8"))
    except (ValueError, zlib.error):
        return {}
    return cues if isinstance(cues, dict) else {}


def _saved_items(request: dict[str, Any]) -> list[Any]:
    saved = request.get("checkpoints")
    if saved is None and request.get("checkpoint") is not None:
        saved = [request["checkpoint"]]
    return saved if isinstance(saved, list) else []


def _frame_bytes(value: Any) -> int:
    """What a JSON value takes in a frame, counting every non-ASCII character as an escape."""
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def _resumed(request: dict[str, Any], count: int, target: str, digest: str) -> dict[int, str]:
    """The cues an earlier attempt finished, from the request's checkpoints, oldest first, a later one winning."""
    found: dict[int, str] = {}
    for item in _saved_items(request):
        data = item.get("data", item) if isinstance(item, dict) else None
        for key, text in _unpack_cues(data, target, digest).items():
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
        config = _own_settings(config)
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
        except _NoAnswer:
            raise PluginError("dependency_unavailable", "Google Translate refused the test request.") from None
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
        digest = source_digest(texts)
        done = _resumed(request, total, target, digest)
        for index, text in done.items():
            outputs[index] = text
        pending = collections.deque(index for index in range(total) if index not in done and _translatable(texts[index]))
        # Cues with nothing to translate (blank, notes, digits only) go back as they came.
        finished = total - len(pending)
        asked = len(pending)  # the cues this attempt has to get text for
        answered = 0  # ... how many of them are done
        produced = 0  # ... and how many of them got some
        empty = refused = partial = tags_lost = 0
        incomplete: set[int] = set()  # cues that have no complete translation: they go back as missing
        unsaved: dict[int, str] = {}  # fully translated cues no accepted checkpoint holds yet
        next_store = CHECKPOINT_EVERY  # store when this many cues are unsaved
        # Room left in a retry request for checkpoints: the budget less its source lines and the
        # checkpoints it already carried.
        room = REQUEST_BUDGET_BYTES - _frame_bytes(lines) - sum(_frame_bytes(item) for item in _saved_items(request))
        stored_out = False  # no more checkpoints fit

        beat = max(0.05, min(1.0, float(ctx.heartbeat_interval or 1.0) / 2))

        async def store() -> None:
            nonlocal next_store, room, stored_out
            batch = dict(unsaved)
            if not batch or stored_out:
                return
            data = pack_cues(batch, target, digest)
            size = _frame_bytes(data) + CHECKPOINT_OVERHEAD_BYTES
            if size > room:
                stored_out = True  # a retry translates these cues again; a checkpoint that does not fit would block it
                return
            # The host acknowledges a checkpoint when it has stored it, which can take a while, so
            # the job keeps its heartbeat going while it waits.
            ack = asyncio.ensure_future(ctx.checkpoint(data))
            try:
                while not (await asyncio.wait({ack}, timeout=beat))[0]:
                    ctx.heartbeat()
                ack.result()
            except PluginError as error:
                if error.code == "cancelled":
                    raise
                # A checkpoint the host refused costs a resume point, not the job. Its cues go in the next one.
                next_store = len(unsaved) + CHECKPOINT_EVERY
                return
            except Exception:
                next_store = len(unsaved) + CHECKPOINT_EVERY  # likewise one that could not be sent
                return
            finally:
                ack.cancel()
            for index in batch:
                unsaved.pop(index, None)
            room -= size
            next_store = CHECKPOINT_EVERY

        throttled = threading.Event()

        stop_flag = threading.Event()

        def stop() -> bool:
            return stop_flag.is_set() or ctx.is_cancelled

        loop = asyncio.get_running_loop()
        running: dict[asyncio.Future, int] = {}
        failure: Exception | None = None
        ctx.progress(finished, total, f"translated {finished} of {total} cues")

        try:
            while (pending or running) and failure is None:
                # Once the probe fills without text, wait for the active cues before asking for more.
                while pending and len(running) < CONCURRENCY and (produced or answered < REFUSAL_PROBE):
                    index = pending.popleft()
                    running[loop.run_in_executor(_pool(), _translate_cue, texts[index], target, stop, throttled)] = index
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
                    answered += 1
                    empty += cue.empty
                    refused += cue.refused
                    partial += cue.partial
                    tags_lost += cue.tags_lost
                    produced += cue.translated
                    if cue.empty or cue.partial:
                        incomplete.add(index)
                    elif cue.translated:
                        unsaved[index] = cue.text
                    if len(unsaved) >= next_store and not stored_out:
                        await store()
                if failure is None and answered >= REFUSAL_PROBE and produced == 0 and not running:
                    # Every dispatched cue came back without text. If every cue was refused, the route is
                    # closed to us. Otherwise the library found no usable text.
                    failure = _Refused() if refused == answered else _NoText()
                ctx.progress(finished, total, f"translated {finished} of {total} cues")
        finally:
            stop_flag.set()
            for future in running:
                future.cancel()

        if failure is not None:
            if unsaved and not ctx.is_cancelled:
                try:
                    await store()
                except PluginError:
                    pass
            raise _failure(failure)
        ctx.raise_if_cancelled()
        if asked and produced == 0:
            if refused == empty:
                # Every cue was refused with an HTTP 4xx: the page route itself is gone or closed to us.
                raise _failure(_Refused())
            raise PluginError("internal", "Google returned no usable text.")

        notes = [f"Translated {total - len(incomplete)} of {total} cues with Google Translate."]
        if tags_lost:
            notes.append(f"{tags_lost} cues came back without their tags.")
        if partial:
            notes.append(f"{partial} cues were only partly translated and are left out.")
        if empty:
            notes.append(f"{empty} cues are left out because Google gave no answer for them.")
        returned = [(index, line) for index, line in enumerate(lines) if index not in incomplete]
        result: dict[str, Any] = {
            "status": "partial" if incomplete else "complete",
            "lines": [_with_text(line, outputs[index], index) for index, line in returned],
            "message": " ".join(notes),
            "tokens": 0,
            "cost": 0.0,
            "model": MODEL,
        }
        if incomplete:
            result["missing"] = [_position(line, index) for index, line in enumerate(lines) if index in incomplete]
        return result
