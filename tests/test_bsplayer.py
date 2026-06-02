import base64
import gzip
import hashlib
import importlib.util
import json
import unittest
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


if __name__ == "__main__":
    unittest.main()
