import base64
import hashlib
import importlib.util
import io
import socket
import unittest
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subclub"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("subclub_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_INCEPTION_HTML = (FIXTURE_DIR / "subclub_search_inception.html").read_bytes()
SEARCH_GOT_HTML = (FIXTURE_DIR / "subclub_search_game_of_thrones.html").read_bytes()
ARCHIVE_INCEPTION_HTML = (FIXTURE_DIR / "subclub_archive_10100.html").read_bytes()
ARCHIVE_GOT_HTML = (FIXTURE_DIR / "subclub_archive_11232.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SubclubParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_row(self):
        rows = self.mod.parse_search_results(SEARCH_INCEPTION_HTML)

        self.assertEqual(rows[0]["archive_id"], "10100")
        self.assertEqual(rows[0]["title"], "Inception")
        self.assertEqual(rows[0]["year"], 2010)
        self.assertIsNone(rows[0]["season"])
        self.assertEqual(rows[0]["imdb_id"], "tt1375666")
        self.assertEqual(rows[0]["fps"], 23.976)
        self.assertEqual(rows[0]["rating"], 5.0)

    def test_parse_search_results_extracts_episode_row(self):
        rows = self.mod.parse_search_results(SEARCH_GOT_HTML)

        self.assertEqual(rows[0]["archive_id"], "11232")
        self.assertEqual(rows[0]["title"], "Game of Thrones")
        self.assertEqual(rows[0]["season"], 1)
        self.assertEqual(rows[0]["episode"], 1)
        self.assertEqual(rows[0]["fps"], 23.976)

    def test_parse_archive_listing_extracts_direct_file_links(self):
        rows = self.mod.parse_archive_listing(ARCHIVE_GOT_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["filename"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertEqual(
            rows[1]["url"],
            "https://www.subclub.eu/down.php?id=11232&filename=R2FtZS5vZi5UaHJvbmVzLlMwMUUwMS43MjBwLkhEVFYueDI2NC1DVFUuc3J0",
        )


class SubclubProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_returns_archive_files(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            SEARCH_INCEPTION_HTML if "jutud.php" in url else ARCHIVE_INCEPTION_HTML
        )

        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
                "resolution": "720p",
                "source": "BluRay",
                "release_group": "CROSSBOW",
            },
            [{"alpha3": "est", "alpha2": "et"}],
            {},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "est")
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["archive_id"], "10100")

    def test_search_episode_returns_matching_release(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            SEARCH_GOT_HTML if "jutud.php" in url else ARCHIVE_GOT_HTML
        )

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
                "release_group": "CTU",
            },
            [{"alpha3": "est", "alpha2": "et"}],
            {},
        )

        self.assertEqual(len(results), 2)
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_ignores_unsupported_language_and_incomplete_video(self):
        provider = self.mod.SubclubProvider()

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "episode", "series": "Game of Thrones"}, [{"alpha3": "est"}], {}), [])

    def test_download_direct_file_normalizes_line_endings(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n"

        content = provider.download(
            {
                "url": "https://www.subclub.eu/down.php?id=11232&filename=abc",
                "archive_id": "11232",
                "filename": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"1\n00:00:01,000 --> 00:00:02,000\nText\n")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", content)
        self.assertNotIn("archive_b64", content)

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.SubclubProvider()
        # Two members but no release signal to disambiguate on (generic synthetic filename,
        # no release_info/release_hints): the worker forwards the raw archive and defers
        # member selection to the host's episode pick.
        body = _zip_body(
            {
                "Game.of.Thrones.S01E01.first.srt": "subtitle a",
                "Game.of.Thrones.S01E01.second.srt": "subtitle b",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=11232",
                "archive_id": "11232",
                "filename": "subclub-11232.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "est", "alpha2": "et"},
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
        provider = self.mod.SubclubProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=11232",
                "archive_id": "11232",
                "filename": "subclub-11232.rar",
                "season": 1,
                "episode": 7,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 7)
        self.assertNotIn("content_b64", content)

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.SubclubProvider()
        body = _zip_body({"Inception.2010.720p.BluRay.srt": "movie subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=10100",
                "archive_id": "10100",
                "filename": "subclub-10100.zip",
                "season": None,
                "episode": None,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertIsNone(content["episode"])

    def test_download_archive_rejects_html_error_page(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "archive_url": "https://www.subclub.eu/down.php?id=11232",
                    "archive_id": "11232",
                    "filename": "subclub-11232.zip",
                },
                {"alpha3": "est", "alpha2": "et"},
                {},
            )

    def test_download_archive_rejects_empty_body(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"   \r\n  "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "archive_url": "https://www.subclub.eu/down.php?id=11232",
                    "archive_id": "11232",
                    "filename": "subclub-11232.zip",
                },
                {"alpha3": "est", "alpha2": "et"},
                {},
            )

    def test_search_synthetic_fallback_carries_release_hints_in_payload(self):
        provider = self.mod.SubclubProvider()
        # Search HTML has a matching episode row, but the archive-content endpoint
        # answers with no subtitle links, forcing the synthetic fallback archive.
        provider._http_get = lambda url, timeout=30, referer=None: (
            SEARCH_GOT_HTML if "jutud.php" in url else b"<html><body>no files</body></html>"
        )

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "resolution": "720p",
                "source": "HDTV",
                "release_group": "CTU",
            },
            [{"alpha3": "est", "alpha2": "et"}],
            {},
        )

        self.assertTrue(results)
        payload = results[0]["provider_payload"]
        self.assertEqual(payload["filename"], "subclub-11232.zip")
        # The host needs episode (and season) to pick the archive member.
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 1)
        # The generic synthetic filename carries no release signal, so the video's release
        # hints are carried into the payload for worker-side member selection.
        self.assertIn("720p", payload["release_hints"])
        self.assertIn("hdtv", payload["release_hints"])
        self.assertIn("ctu", payload["release_hints"])

        # Happy path: the synthetic archive holds both releases of the same episode; the
        # worker pins the member matching the scored release hints (HDTV/CTU), not BluRay.
        body = _zip_body(
            {
                "Game.of.Thrones.S01E01.720p.BluRay.X264-REWARD.srt": "wrong release",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right release",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body
        content = provider.download(payload, {"alpha3": "est", "alpha2": "et"}, {})

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["member"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        # Member pinned, so episode selection is not deferred to the host.
        self.assertNotIn("episode", content)
        self.assertNotIn("content_b64", content)


class SubclubMemberSelectionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _download_archive(self, files, payload):
        provider = self.mod.SubclubProvider()
        body = _zip_body(files)
        provider._http_get = lambda url, timeout=30, referer=None: body
        base = {"archive_url": "https://www.subclub.eu/down.php?id=1", "archive_id": "1"}
        content = provider.download({**base, **payload}, {"alpha3": "est", "alpha2": "et"}, {})
        return content, body

    def test_pins_member_matching_release_hints(self):
        # Two releases of the requested episode; the release hints (HDTV/CTU) pick one.
        content, body = self._download_archive(
            {
                "Game.of.Thrones.S01E01.720p.BluRay.x264-REWARD.srt": "wrong",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right",
            },
            {
                "filename": "subclub-1.zip",
                "release_hints": "720p.hdtv.ctu",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertEqual(content["member"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertNotIn("episode", content)
        self.assertEqual(base64.b64decode(content["archive_b64"]), body)

    def test_season_pack_narrows_by_season_and_episode(self):
        # A pack that repeats episode 5 across two seasons must be matched on BOTH axes.
        # Episode-only narrowing would leave both S01E05 and S02E05 in the pool.
        content, _ = self._download_archive(
            {
                "Show.S01E05.HDTV.x264-GRP.srt": "s1",
                "Show.S02E05.HDTV.x264-GRP.srt": "s2",
            },
            {
                "filename": "subclub-1.zip",
                "release_hints": "hdtv.grp",
                "season": 2,
                "episode": 5,
            },
        )

        # Only S02E05 survives the season+episode narrowing, so it is pinned even though
        # both members share the same release hints.
        self.assertEqual(content["member"], "Show.S02E05.HDTV.x264-GRP.srt")

    def test_separated_sxxexx_token_matches(self):
        # S01.E02 / S01 E02 / S01-E02 / 1x02 must all be recognised as the episode marker.
        # Two members share episode 2 (the wanted one via a SEPARATED marker), so episode
        # narrowing keeps both and the release hint (CTU) pins the separated-marker member.
        for marker in ("S01.E02", "S01 E02", "S01-E02", "1x02"):
            content, _ = self._download_archive(
                {
                    f"Show.{marker}.HDTV-CTU.srt": "wanted",
                    "Show.S01E02.HDTV-REWARD.srt": "other release of same episode",
                },
                {
                    "filename": "subclub-1.zip",
                    "release_hints": "ctu",
                    "season": 1,
                    "episode": 2,
                },
            )
            self.assertEqual(content["member"], f"Show.{marker}.HDTV-CTU.srt", marker)

    def test_defers_on_release_hint_tie(self):
        # Two members of the requested episode share the hint score: no confident winner,
        # so defer to host episode selection rather than pin a coin-flip.
        content, _ = self._download_archive(
            {
                "Show.S01E01.720p.HDTV.srt": "a",
                "Show.S01E01.720p.HDTV.srt.copy.srt": "b",
            },
            {
                "filename": "subclub-1.zip",
                "release_hints": "720p.hdtv",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 1)

    def test_defers_when_requested_episode_absent(self):
        # The wanted episode is in no member; pinning a wrong-episode member would hard-fail
        # the host download, so defer instead of mispicking.
        content, _ = self._download_archive(
            {
                "Show.S01E01.HDTV-CTU.srt": "a",
                "Show.S01E02.HDTV-CTU.srt": "b",
            },
            {
                "filename": "subclub-1.zip",
                "release_hints": "hdtv.ctu",
                "season": 1,
                "episode": 7,
            },
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 7)

    def test_excludes_macosx_sidecar_and_dotfiles(self):
        # Sidecar/dotfiles and directory entries must never be pinned.
        content, _ = self._download_archive(
            {
                "__MACOSX/._Show.S01E01.720p.HDTV.x264-CTU.srt": "junk",
                ".DS_Store": "junk",
                "subs/": "",
                "Show.S01E01.720p.HDTV.x264-CTU.srt": "real",
                "Show.S01E01.720p.BluRay.x264-REWARD.srt": "other",
            },
            {
                "filename": "subclub-1.zip",
                "release_hints": "720p.hdtv.ctu",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertEqual(content["member"], "Show.S01E01.720p.HDTV.x264-CTU.srt")

    def test_three_digit_code_does_not_match_resolution_token(self):
        # S07E20 ("720") must NOT match "720p", and the wanted episode is genuinely absent,
        # so the download defers instead of mispicking the 720p member.
        content, _ = self._download_archive(
            {
                "Show.720p.HDTV.x264-CTU.srt": "a",
                "Show.1080p.HDTV.x264-CTU.srt": "b",
            },
            {
                "filename": "subclub-1.zip",
                "release_hints": "hdtv.ctu",
                "season": 7,
                "episode": 20,
            },
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 20)

    def test_defers_without_release_hints(self):
        # No release signal at all: nothing to break the tie, so defer to the host.
        content, _ = self._download_archive(
            {
                "Show.S01E01.first.srt": "a",
                "Show.S01E01.second.srt": "b",
            },
            {"filename": "subclub-1.zip", "season": 1, "episode": 1},
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 1)

    def test_rar_archive_defers_to_host_episode(self):
        # RAR is not stdlib-listable; always defer to the host episode pick.
        provider = self.mod.SubclubProvider()
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=1",
                "archive_id": "1",
                "filename": "subclub-1.zip",
                "release_hints": "720p.hdtv.ctu",
                "season": 1,
                "episode": 3,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 3)

    def test_movie_archive_single_member_defers(self):
        # A movie archive (episode None) with one member has nothing to disambiguate.
        content, _ = self._download_archive(
            {"Inception.2010.720p.BluRay.srt": "movie"},
            {"filename": "subclub-1.zip", "release_hints": "720p.bluray", "season": None, "episode": None},
        )

        self.assertNotIn("member", content)
        self.assertIsNone(content["episode"])


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._body


class _FakeOpener:
    """Drop-in for the provider's cookie-aware opener that scripts a sequence of
    transport outcomes: an exception class/instance is raised, anything else is
    returned as a response body."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException) or (
            isinstance(outcome, type) and issubclass(outcome, BaseException)
        ):
            raise outcome
        return _FakeResponse(outcome)


def _http_error(code, retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://www.subclub.eu/jutud.php", code, "boom", headers, io.BytesIO(b"")
    )


class SubclubTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.slept = []
        self.mod.time.sleep = lambda seconds: self.slept.append(seconds)

    def _provider_with(self, outcomes):
        provider = self.mod.SubclubProvider()
        opener = _FakeOpener(outcomes)
        provider._opener = opener
        return provider, opener

    def test_retries_url_error_then_succeeds(self):
        provider, opener = self._provider_with(
            [urllib.error.URLError("connection reset"), b"<html>ok</html>"]
        )

        body = provider._http_get("https://www.subclub.eu/jutud.php")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len(self.slept), 1)

    def test_retries_503_twice_then_succeeds(self):
        provider, opener = self._provider_with(
            [_http_error(503), _http_error(503), b"<html>ok</html>"]
        )

        body = provider._http_get("https://www.subclub.eu/jutud.php")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(self.slept), 2)
        # Exponential backoff: 0.5 then 1.0 seconds.
        self.assertEqual(self.slept, [0.5, 1.0])

    def test_429_honors_retry_after_header(self):
        provider, opener = self._provider_with(
            [_http_error(429, retry_after="3"), b"<html>ok</html>"]
        )

        body = provider._http_get("https://www.subclub.eu/jutud.php")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(opener.calls, 2)
        # Retry-After (3s) wins over the 0.5s base backoff but stays under the cap.
        self.assertEqual(self.slept, [3.0])

    def test_404_is_not_retried(self):
        provider, opener = self._provider_with([_http_error(404)])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.subclub.eu/jutud.php")

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(opener.calls, 1)
        self.assertEqual(self.slept, [])

    def test_403_is_not_retried(self):
        provider, opener = self._provider_with([_http_error(403)])

        with self.assertRaises(urllib.error.HTTPError):
            provider._http_get("https://www.subclub.eu/jutud.php")

        self.assertEqual(opener.calls, 1)
        self.assertEqual(self.slept, [])

    def test_gives_up_after_max_attempts(self):
        provider, opener = self._provider_with(
            [socket.timeout(), socket.timeout(), socket.timeout()]
        )

        with self.assertRaises(socket.timeout):
            provider._http_get("https://www.subclub.eu/jutud.php")

        # 1 initial attempt + 2 retries = 3 transport calls, then it gives up.
        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(self.slept), 2)


if __name__ == "__main__":
    unittest.main()
