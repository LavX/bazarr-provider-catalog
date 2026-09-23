import base64
import hashlib
import importlib.util
import json
import socket
import unittest
import urllib.error
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subtitulamostv"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subtitulamostv_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(name):
    return (FIXTURE_DIR / name).read_bytes()


def _video_fixture():
    return json.loads(
        (FIXTURE_DIR / "subtitulamostv_video_the_last_of_us_s01e01.json").read_text()
    )


class QueryAndSearchParsingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_with_year_emits_year_query_then_series_query(self):
        queries = self.mod.build_queries(_video_fixture())
        self.assertEqual(queries, ["The Last of Us (2023)", "The Last of Us"])

    def test_movies_and_incomplete_episodes_do_not_search(self):
        self.assertEqual(
            self.mod.build_queries({"kind": "movie", "title": "The Last of Us"}),
            [],
        )
        self.assertEqual(
            self.mod.build_queries({"kind": "episode", "series": "The Last of Us"}),
            [],
        )

    def test_normalized_title_strips_trailing_year(self):
        self.assertEqual(
            self.mod.normalize_search_title("The Last of Us (2023)"),
            "the last of us",
        )

    def test_search_results_filter_to_exact_normalized_show_name(self):
        body = _fixture("subtitulamostv_search_the_last_of_us.json")
        results = self.mod.filter_exact_show_results(
            self.mod.parse_search_results(body),
            "The Last of Us (2023)",
        )
        self.assertEqual([item["show_id"] for item in results], ["101"])

    def test_invalid_search_json_returns_empty(self):
        self.assertEqual(self.mod.parse_search_results(b"not json"), [])


class LanguageMappingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_common_site_labels_map_to_payloads(self):
        self.assertEqual(
            self.mod.site_language_to_payload("English")["alpha3"],
            "eng",
        )
        self.assertEqual(
            self.mod.site_language_to_payload("Català")["alpha3"],
            "cat",
        )
        self.assertEqual(
            self.mod.site_language_to_payload("Galego")["alpha3"],
            "glg",
        )
        self.assertEqual(
            self.mod.site_language_to_payload("Brazilian")["country_alpha2"],
            "BR",
        )

    def test_latin_american_spanish_preserves_requested_country_variant(self):
        requested = {
            "alpha3": "spa",
            "alpha2": "es",
            "country_alpha2": "AR",
            "ietf": "es-AR",
        }
        mapped = self.mod.site_language_to_payload(
            "Español (Latinoamérica)",
            [requested],
        )
        self.assertEqual(mapped, requested)

    def test_spain_spanish_label_matches_generic_spanish_request(self):
        requested = {"alpha3": "spa", "alpha2": "es"}
        mapped = self.mod.site_language_to_payload(
            "Español (España)",
            [requested],
        )
        self.assertEqual(mapped, requested)

    def test_unrequested_language_returns_none(self):
        requested = [{"alpha3": "eng", "alpha2": "en"}]
        self.assertIsNone(
            self.mod.site_language_to_payload("Portuguese", requested)
        )


class EpisodePageParsingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_choice_parser_finds_selected_and_unselected_links(self):
        choices = self.mod.parse_choice_links(
            _fixture("subtitulamostv_show_the_last_of_us.html"),
            "episode-choices",
        )
        self.assertEqual([choice["number"] for choice in choices], [1, 2])
        self.assertEqual(choices[0]["href"], "/shows/101/seasons/1/episodes/1")
        self.assertFalse(choices[0]["selected"])

    def test_episode_page_returns_requested_languages_and_skips_unavailable(self):
        requested = [
            {"alpha3": "eng", "alpha2": "en"},
            {
                "alpha3": "spa",
                "alpha2": "es",
                "country_alpha2": "AR",
                "ietf": "es-AR",
            },
        ]
        rows = self.mod.parse_episode_page(
            _fixture("subtitulamostv_episode_the_last_of_us_s01e01.html"),
            requested,
            self.mod.BASE_URL + "/shows/101/seasons/1/episodes/1",
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["language"]["alpha3"], "eng")
        self.assertEqual(rows[0]["release_info"], "WEB-DL.x264-NTb")
        self.assertEqual(
            rows[0]["download_url"],
            "https://www.subtitulamos.tv/download/101/s01e01/en",
        )
        self.assertEqual(rows[1]["language"]["country_alpha2"], "AR")
        self.assertNotIn("Unavailable release", [row["release_info"] for row in rows])


class ProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_routes(self):
        provider = self.mod.SubtitulamosTVProvider()
        calls = []
        routes = {
            "https://www.subtitulamos.tv/search/query?q=The+Last+of+Us+%282023%29":
                _fixture("subtitulamostv_search_year_empty.json"),
            "https://www.subtitulamos.tv/search/query?q=The+Last+of+Us":
                _fixture("subtitulamostv_search_the_last_of_us.json"),
            "https://www.subtitulamos.tv/shows/101":
                _fixture("subtitulamostv_show_the_last_of_us.html"),
            "https://www.subtitulamos.tv/shows/101/seasons/1/episodes/1":
                _fixture("subtitulamostv_episode_the_last_of_us_s01e01.html"),
        }

        def fake_get(url, timeout=None, referer=None):
            del timeout, referer
            calls.append(url)
            if url not in routes:
                raise AssertionError(f"unexpected URL: {url}")
            return routes[url]

        provider._http_get = fake_get
        return provider, calls

    def test_search_falls_back_from_year_query_and_returns_worker_candidates(self):
        provider, calls = self._provider_with_routes()
        results = provider.search(
            _video_fixture(),
            [
                {"alpha3": "eng", "alpha2": "en"},
                {
                    "alpha3": "spa",
                    "alpha2": "es",
                    "country_alpha2": "AR",
                    "ietf": "es-AR",
                },
            ],
            {},
        )

        self.assertEqual(
            calls[:2],
            [
                "https://www.subtitulamos.tv/search/query?q=The+Last+of+Us+%282023%29",
                "https://www.subtitulamos.tv/search/query?q=The+Last+of+Us",
            ],
        )
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["provider"], "subtitulamostv")
        self.assertEqual(
            first["id"],
            "https://www.subtitulamos.tv/download/101/s01e01/en",
        )
        self.assertEqual(first["release_info"], "WEB-DL.x264-NTb")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertEqual(first["matches"], ["episode", "season", "series", "title"])
        self.assertFalse(first["hash_verifiable"])
        self.assertEqual(
            first["page_link"],
            "https://www.subtitulamos.tv/shows/101/seasons/1/episodes/1",
        )
        self.assertEqual(
            first["provider_payload"]["download_url"],
            "https://www.subtitulamos.tv/download/101/s01e01/en",
        )

    def test_search_tries_later_exact_show_hits_when_first_has_no_episode(self):
        provider = self.mod.SubtitulamosTVProvider()
        calls = []
        search_body = json.dumps(
            [
                {"show_id": "201", "show_name": "The Last of Us"},
                {"show_id": "101", "show_name": "The Last of Us"},
            ]
        ).encode("utf-8")
        first_show_body = b"""
        <nav id="season-choices">
          <a class="choice selected" href="/shows/201/seasons/1">Season 1</a>
        </nav>
        <nav id="episode-choices">
          <a class="choice" href="/shows/201/seasons/1/episodes/2">Episode 2</a>
        </nav>
        """
        routes = {
            "https://www.subtitulamos.tv/search/query?q=The+Last+of+Us": search_body,
            "https://www.subtitulamos.tv/shows/201": first_show_body,
            "https://www.subtitulamos.tv/shows/101":
                _fixture("subtitulamostv_show_the_last_of_us.html"),
            "https://www.subtitulamos.tv/shows/101/seasons/1/episodes/1":
                _fixture("subtitulamostv_episode_the_last_of_us_s01e01.html"),
        }

        def fake_get(url, timeout=None, referer=None):
            del timeout, referer
            calls.append(url)
            if url not in routes:
                raise AssertionError(f"unexpected URL: {url}")
            return routes[url]

        provider._http_get = fake_get
        results = provider.search(
            {
                "kind": "episode",
                "series": "The Last of Us",
                "season": 1,
                "episode": 1,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(results[0]["provider"], "subtitulamostv")
        self.assertIn("https://www.subtitulamos.tv/shows/201", calls)
        self.assertIn("https://www.subtitulamos.tv/shows/101", calls)

    def test_search_returns_empty_for_unsupported_language(self):
        provider, calls = self._provider_with_routes()
        results = provider.search(_video_fixture(), [{"alpha3": "deu", "alpha2": "de"}], {})
        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_search_returns_empty_for_movie(self):
        provider, calls = self._provider_with_routes()
        results = provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng"}], {})
        self.assertEqual(results, [])
        self.assertEqual(calls, [])


class ProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_base64_payload_with_sha256(self):
        provider = self.mod.SubtitulamosTVProvider()
        body = _fixture("subtitulamostv_download_en.srt").replace(b"\n", b"\r\n")

        def fake_get(url, timeout=None, referer=None):
            self.assertEqual(url, "https://www.subtitulamos.tv/download/101/s01e01/en")
            self.assertEqual(
                referer,
                "https://www.subtitulamos.tv/shows/101/seasons/1/episodes/1",
            )
            return body

        provider._http_get = fake_get
        result = provider.download(
            {
                "provider": "subtitulamostv",
                "download_url": "https://www.subtitulamos.tv/download/101/s01e01/en",
                "page_url": "https://www.subtitulamos.tv/shows/101/seasons/1/episodes/1",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, body.replace(b"\r\n", b"\n"))
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertEqual(result["encoding"], "utf-8")

    def test_download_requires_url(self):
        provider = self.mod.SubtitulamosTVProvider()
        with self.assertRaises(ValueError):
            provider.download({}, {"alpha3": "eng"}, {})


class UrlEncodingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_url_encodes_parentheses_and_spaces(self):
        url = self.mod.build_search_url("The Last of Us (2023)")
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.subtitulamos.tv")
        self.assertEqual(parsed.path, "/search/query")
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {"q": ["The Last of Us (2023)"]})


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code):
    return urllib.error.HTTPError(
        url="https://www.subtitulamos.tv/x",
        code=code,
        msg=f"status {code}",
        hdrs=None,
        fp=None,
    )


def _http_error_with_retry_after(code, retry_after):
    import email.message

    headers = email.message.Message()
    headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        url="https://www.subtitulamos.tv/x",
        code=code,
        msg=f"status {code}",
        hdrs=headers,
        fp=None,
    )


class TransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.sleeps = []
        self._http_errors = []
        self._orig_sleep = self.mod.time.sleep
        self._orig_urlopen = self.mod.urllib.request.urlopen
        self.mod.time.sleep = self.sleeps.append

    def tearDown(self):
        self.mod.time.sleep = self._orig_sleep
        self.mod.urllib.request.urlopen = self._orig_urlopen
        # urllib.error.HTTPError opens an internal buffer; close ours so the
        # interpreter does not emit a ResourceWarning during GC.
        for error in self._http_errors:
            error.close()

    def _patch_urlopen(self, sequence):
        calls = {"count": 0}
        for item in sequence:
            if isinstance(item, urllib.error.HTTPError):
                self._http_errors.append(item)

        def fake_urlopen(request, timeout=None):
            del request, timeout
            calls["count"] += 1
            item = sequence[calls["count"] - 1]
            if isinstance(item, Exception):
                raise item
            return _FakeResponse(item)

        self.mod.urllib.request.urlopen = fake_urlopen
        return calls

    def test_retries_url_error_then_succeeds(self):
        calls = self._patch_urlopen(
            [urllib.error.URLError("connection refused"), b"ok-body"]
        )
        result = self.mod._http_get("https://www.subtitulamos.tv/search/query")
        self.assertEqual(result, b"ok-body")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_retries_503_then_succeeds(self):
        calls = self._patch_urlopen(
            [_http_error(503), _http_error(503), b"ok-body"]
        )
        result = self.mod._http_get("https://www.subtitulamos.tv/search/query")
        self.assertEqual(result, b"ok-body")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_timeout_is_retried(self):
        calls = self._patch_urlopen([socket.timeout("read timed out"), b"ok-body"])
        result = self.mod._http_get("https://www.subtitulamos.tv/search/query")
        self.assertEqual(result, b"ok-body")
        self.assertEqual(calls["count"], 2)

    def test_404_is_not_retried(self):
        calls = self._patch_urlopen([_http_error(404), b"ok-body"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.mod._http_get("https://www.subtitulamos.tv/search/query")
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.sleeps, [])

    def test_429_honors_retry_after_header(self):
        calls = self._patch_urlopen(
            [_http_error_with_retry_after(429, 3), b"ok-body"]
        )
        result = self.mod._http_get("https://www.subtitulamos.tv/search/query")
        self.assertEqual(result, b"ok-body")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(self.sleeps, [3])

    def test_persistent_transient_error_propagates_after_max_attempts(self):
        calls = self._patch_urlopen(
            [
                urllib.error.URLError("reset"),
                urllib.error.URLError("reset"),
                urllib.error.URLError("reset"),
            ]
        )
        with self.assertRaises(urllib.error.URLError):
            self.mod._http_get("https://www.subtitulamos.tv/search/query")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.sleeps), 2)


if __name__ == "__main__":
    unittest.main()
