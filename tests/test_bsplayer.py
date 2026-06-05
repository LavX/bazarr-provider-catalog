import base64
import email.message
import gzip
import hashlib
import importlib.util
import io
import json
import socket
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "bsplayer"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "bsplayer_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = json.loads((FIXTURE_DIR / "bsplayer_video_hash_movie.json").read_text())

LOGIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://api.bsplayer-subtitles.com/v1.php" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SOAP-ENV:Body>
    <ns1:logInResponse>
      <return xsi:type="ns1:SubtitlesResult">
        <result xsi:type="xsd:string">200</result>
        <status xsi:type="xsd:string">OK</status>
        <data xsi:type="xsd:string">token-value</data>
      </return>
    </ns1:logInResponse>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
""".encode()

SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://api.bsplayer-subtitles.com/v1.php" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SOAP-ENV:Body>
    <ns1:searchSubtitlesResponse>
      <return xsi:type="ns1:SearchResult">
        <result xsi:type="ns1:SubtitlesResult">
          <result xsi:type="xsd:string">200</result>
          <status xsi:type="xsd:string">OK</status>
          <data xsi:type="xsd:string">1</data>
        </result>
        <data SOAP-ENC:arrayType="ns1:SubtitleData[1]" xsi:type="ns1:ArrayOfSubtitleData">
          <item xsi:type="ns1:SubtitleData">
            <subID xsi:type="xsd:int">1056176</subID>
            <subDownloadLink xsi:type="xsd:string">http://download.bsplayer-subtitles.com/download/file/1056176/abcdef</subDownloadLink>
            <subLang xsi:type="xsd:string">eng</subLang>
            <subName xsi:type="xsd:string">Example.Movie.2024.WEB-DL.srt</subName>
            <subFormat xsi:type="xsd:string">srt</subFormat>
          </item>
        </data>
      </return>
    </ns1:searchSubtitlesResponse>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
""".encode()

EMPTY_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://api.bsplayer-subtitles.com/v1.php" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SOAP-ENV:Body>
    <ns1:searchSubtitlesResponse>
      <return xsi:type="ns1:SearchResult">
        <result xsi:type="ns1:SubtitlesResult">
          <status xsi:type="xsd:string">OK</status>
          <data xsi:type="xsd:string">0</data>
        </result>
        <data />
      </return>
    </ns1:searchSubtitlesResponse>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
""".encode()

LOGOUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
  <SOAP-ENV:Body><return><status>OK</status></return></SOAP-ENV:Body>
</SOAP-ENV:Envelope>
""".encode()


class FakeClient:
    def __init__(self, search_xml=SEARCH_XML, download_body=None):
        self.calls = []
        self.search_xml = search_xml
        self.download_body = download_body or gzip.compress(b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")

    def request(self, func_name, params):
        self.calls.append((func_name, params))
        if func_name == "logIn":
            return LOGIN_XML
        if func_name == "searchSubtitles":
            return self.search_xml
        if func_name == "logOut":
            return LOGOUT_XML
        raise AssertionError(f"unexpected SOAP call: {func_name}")

    def get_bytes(self, url):
        self.calls.append(("GET", url))
        return self.download_body


class BSPlayerParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_language_codes_match_bsplayer_api(self):
        self.assertEqual(self.mod.bsplayer_language_code({"alpha3": "eng"}), "eng")
        self.assertEqual(self.mod.bsplayer_language_code({"alpha3": "deu"}), "ger")
        self.assertEqual(self.mod.bsplayer_language_code({"alpha3": "ces"}), "cze")
        self.assertEqual(
            self.mod.bsplayer_language_code({"alpha3": "por", "country": "BR"}),
            "pob",
        )

    def test_extract_login_token(self):
        self.assertEqual(self.mod.parse_login_token(LOGIN_XML), "token-value")

    def test_parse_search_response(self):
        items = self.mod.parse_search_response(SEARCH_XML)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sub_id"], "1056176")
        self.assertEqual(items[0]["download_url"], "http://download.bsplayer-subtitles.com/download/file/1056176/abcdef")
        self.assertEqual(items[0]["language_code"], "eng")
        self.assertEqual(items[0]["filename"], "Example.Movie.2024.WEB-DL.srt")

    def test_parse_empty_search_response(self):
        self.assertEqual(self.mod.parse_search_response(EMPTY_SEARCH_XML), [])


class BSPlayerProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_hash_and_size(self):
        client = FakeClient()
        provider = self.mod.BSPlayerProvider(api_client=client)

        self.assertEqual(provider.search({"kind": "movie", "size": 1}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "movie", "hashes": {"bsplayer": "abc"}}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(client.calls, [])

    def test_search_returns_hash_result(self):
        client = FakeClient()
        provider = self.mod.BSPlayerProvider(api_client=client)

        results = provider.search(VIDEO, [{"alpha3": "eng", "alpha2": "en"}], {})

        self.assertEqual([call[0] for call in client.calls], ["logIn", "searchSubtitles", "logOut"])
        self.assertIn("<movieHash>0000000000000000</movieHash>", client.calls[1][1])
        self.assertIn("<movieSize>123456789</movieSize>", client.calls[1][1])
        self.assertIn("<languageId>eng</languageId>", client.calls[1][1])
        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["provider"], "bsplayer")
        self.assertEqual(candidate["id"], "bsplayer-1056176")
        self.assertEqual(candidate["language"]["alpha3"], "eng")
        self.assertTrue(candidate["hash_verifiable"])
        self.assertEqual(candidate["matches"], ["title", "year", "hash"])
        self.assertEqual(candidate["provider_payload"]["download_url"], "http://download.bsplayer-subtitles.com/download/file/1056176/abcdef")

    def test_search_returns_empty_when_api_has_no_items(self):
        provider = self.mod.BSPlayerProvider(api_client=FakeClient(search_xml=EMPTY_SEARCH_XML))

        self.assertEqual(provider.search(VIDEO, [{"alpha3": "eng"}], {}), [])


class BSPlayerProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_decompresses_gzip_subtitle(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        provider = self.mod.BSPlayerProvider(api_client=FakeClient(download_body=gzip.compress(body)))

        result = provider.download(
            {
                "provider": "bsplayer",
                "schema": 1,
                "download_url": "http://download.bsplayer-subtitles.com/download/file/1056176/abcdef",
                "filename": "Example.Movie.2024.WEB-DL.srt",
                "format": "srt",
            },
            {"alpha3": "eng"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])

    def test_download_rejects_wrong_provider_payload(self):
        provider = self.mod.BSPlayerProvider(api_client=FakeClient())

        with self.assertRaises(ValueError):
            provider.download({"provider": "other", "download_url": "http://example.test/a.gz"}, {"alpha3": "eng"}, {})


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class BSPlayerTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.sleeps = []
        self._http_errors = []
        # urllib and time are shared module singletons, so save and restore the
        # originals to avoid leaking the monkeypatch into other test cases.
        self._orig_urlopen = self.mod.urllib.request.urlopen
        self._orig_sleep = self.mod.time.sleep
        # Make the backoff sleep a mockable no-op via the module-level time.sleep.
        self.mod.time.sleep = lambda seconds: self.sleeps.append(seconds)

    def tearDown(self):
        self.mod.urllib.request.urlopen = self._orig_urlopen
        self.mod.time.sleep = self._orig_sleep
        # HTTPError keeps an internal tempfile open; close it to avoid noisy
        # ResourceWarning during garbage collection.
        for error in self._http_errors:
            error.close()

    def _http_error(self, code, body=b"", headers=None):
        error = urllib.error.HTTPError(
            url="http://s1.api.bsplayer-subtitles.com/v1.php",
            code=code,
            msg="error",
            hdrs=headers,
            fp=io.BytesIO(body),
        )
        self._http_errors.append(error)
        return error

    def _patch_urlopen(self, side_effects):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            index = calls["count"]
            calls["count"] += 1
            effect = side_effects[index]
            if isinstance(effect, Exception):
                raise effect
            return _FakeResponse(effect)

        self.mod.urllib.request.urlopen = fake_urlopen
        return calls

    def test_retries_url_error_then_succeeds(self):
        request = self.mod.urllib.request.Request("http://s1.api.bsplayer-subtitles.com/v1.php")
        calls = self._patch_urlopen([urllib.error.URLError("connection reset"), b"OK"])

        result = self.mod._urlopen_read(request, timeout=12)

        self.assertEqual(result, b"OK")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_retries_503_twice_then_succeeds(self):
        request = self.mod.urllib.request.Request("http://s1.api.bsplayer-subtitles.com/v1.php")
        calls = self._patch_urlopen([self._http_error(503), self._http_error(503), b"DATA"])

        result = self.mod._urlopen_read(request, timeout=12)

        self.assertEqual(result, b"DATA")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_retries_socket_timeout(self):
        request = self.mod.urllib.request.Request("http://s1.api.bsplayer-subtitles.com/v1.php")
        calls = self._patch_urlopen([socket.timeout("timed out"), b"OK"])

        result = self.mod._urlopen_read(request, timeout=12)

        self.assertEqual(result, b"OK")
        self.assertEqual(calls["count"], 2)

    def test_does_not_retry_404(self):
        request = self.mod.urllib.request.Request("http://s1.api.bsplayer-subtitles.com/v1.php")
        calls = self._patch_urlopen([self._http_error(404), b"unused"])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.mod._urlopen_read(request, timeout=12)

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.sleeps, [])

    def test_gives_up_after_max_attempts_and_reraises(self):
        request = self.mod.urllib.request.Request("http://s1.api.bsplayer-subtitles.com/v1.php")
        calls = self._patch_urlopen(
            [urllib.error.URLError("a"), urllib.error.URLError("b"), urllib.error.URLError("c")]
        )

        with self.assertRaises(urllib.error.URLError):
            self.mod._urlopen_read(request, timeout=12)

        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_honors_retry_after_header_on_429(self):
        request = self.mod.urllib.request.Request("http://s1.api.bsplayer-subtitles.com/v1.php")
        headers = email.message.Message()
        headers["Retry-After"] = "3"
        self._patch_urlopen([self._http_error(429, headers=headers), b"OK"])

        result = self.mod._urlopen_read(request, timeout=12)

        self.assertEqual(result, b"OK")
        self.assertEqual(self.sleeps, [3])

    def test_get_bytes_retries_transient_then_returns(self):
        client = self.mod.BSPlayerApiClient(api_url="http://s1.api.bsplayer-subtitles.com/v1.php")
        self._patch_urlopen([self._http_error(503), b"payload"])

        self.assertEqual(client.get_bytes("http://download.test/file"), b"payload")
        self.assertEqual(len(self.sleeps), 1)


if __name__ == "__main__":
    unittest.main()
