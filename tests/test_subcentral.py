import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subcentral"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subcentral_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOME_HTML = (FIXTURE_DIR / "subcentral_home.html").read_bytes()
BOARD_HTML = (FIXTURE_DIR / "subcentral_board_blue_lights.html").read_bytes()
THREAD_HTML = (FIXTURE_DIR / "subcentral_thread_blue_lights.html").read_bytes()
THANK_XML = (FIXTURE_DIR / "subcentral_thank_blue_lights.xml").read_bytes()
RAR_BODY = (FIXTURE_DIR / "subcentral_blue_lights_ion10.rar").read_bytes()


def _thread_link(thread_id, title):
    return (
        f'<a href="index.php?page=Thread&amp;threadID={thread_id}&amp;s=ignored">'
        f"{title}</a>"
    ).encode("utf-8")


def _visible_attachment(language_flag, attachment_id, hash_value="a" * 40):
    return f"""
        <div id="a1" style="display:block;">
          <table>
            <thead>
              <tr><th>Episode</th><th><img src="creative/bilder/flags/{language_flag}.png" /> WEB</th></tr>
            </thead>
            <tbody>
              <tr class="aktiv">
                <td class="release">E01 - "The Code"</td>
                <td><a href="index.php?page=Attachment&amp;attachmentID={attachment_id}&amp;h={hash_value}">ION10</a></td>
              </tr>
            </tbody>
          </table>
        </div>
    """.encode("utf-8")


class SubCentralParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_series_options_extracts_board_links(self):
        rows = self.mod.parse_series_options(HOME_HTML)
        self.assertEqual(rows[0]["title"], "Blue Lights")
        self.assertEqual(rows[0]["board_id"], "1253")
        self.assertEqual(rows[0]["url"], "https://www.subcentral.de/index.php?page=Board&boardID=1253")

    def test_parse_board_threads_extracts_subtitle_thread(self):
        rows = self.mod.parse_board_threads(BOARD_HTML, "Blue Lights")
        self.assertEqual(rows[0]["thread_id"], "52021")
        self.assertEqual(rows[0]["season"], 1)
        self.assertEqual(rows[0]["url"], "https://www.subcentral.de/index.php?page=Thread&threadID=52021")

    def test_parse_board_threads_includes_vo_only_subtitle_thread(self):
        body = _thread_link(
            "52099",
            "Blue Lights - Staffel 1 - [VO-Subs: 06 | Aired: 06/06]",
        )
        rows = self.mod.parse_board_threads(body, "Blue Lights")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["thread_id"], "52099")

    def test_parse_thread_gate_extracts_guest_thank_request(self):
        gate = self.mod.parse_thread_gate(THREAD_HTML)
        self.assertEqual(gate["post_id"], "568110")
        self.assertEqual(gate["token"], "security-token")
        self.assertEqual(gate["sid"], "session-cookie-hash")
        self.assertEqual(
            gate["thank_url"],
            "https://www.subcentral.de/index.php?action=Thank&output=xml&postID=568110&t=security-token&s=session-cookie-hash",
        )

    def test_parse_thread_gate_accepts_empty_guest_sid_and_prefers_thank_button(self):
        body = b"""
            <script type="text/javascript">
                var SID_ARG_2ND = '';
                var SECURITY_TOKEN = 'live-token';
            </script>
            <a href="index.php?page=Thread&postID=33814#post33814">old post</a>
            <a name="thankPostButton568110"
               href="index.php?action=Thank&amp;postID=568110&amp;t=live-token"
               onClick="thankOmat.thankPost(568110); return false;">Bedanken</a>
        """
        gate = self.mod.parse_thread_gate(body)
        self.assertEqual(gate["post_id"], "568110")
        self.assertEqual(gate["sid"], "")
        self.assertEqual(
            gate["thank_url"],
            "https://www.subcentral.de/index.php?action=Thank&output=xml&postID=568110&t=live-token",
        )

    def test_parse_revealed_attachments_extracts_languages_and_episode(self):
        rows = self.mod.parse_revealed_attachments(THANK_XML)
        german = rows[0]
        english = rows[1]
        self.assertEqual(german["language"], "deu")
        self.assertEqual(german["episode"], 1)
        self.assertEqual(german["attachment_id"], "300350")
        self.assertEqual(german["release_group"], "ION10")
        self.assertEqual(english["language"], "eng")
        self.assertEqual(english["attachment_id"], "300168")


class SubCentralProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_skips_thread_with_missing_thank_gate(self):
        provider = self.mod.SubCentralProvider()
        board_html = b"\n".join(
            [
                _thread_link("11111", "Blue Lights - Staffel 1 - [DE-Subs: 01 | Aired: 01/06]"),
                _thread_link("52021", "Blue Lights - Staffel 1 - [DE-Subs: 02 | Aired: 06/06]"),
            ]
        )
        responses = {
            "https://www.subcentral.de/": HOME_HTML,
            "https://www.subcentral.de/index.php?page=Board&boardID=1253": board_html,
            "https://www.subcentral.de/index.php?page=Thread&threadID=11111": b"<html>no attachments or thank gate</html>",
            "https://www.subcentral.de/index.php?page=Thread&threadID=52021": THREAD_HTML,
            "https://www.subcentral.de/index.php?action=Thank&output=xml&postID=568110&t=security-token&s=session-cookie-hash": THANK_XML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1},
            [{"alpha3": "deu", "alpha2": "de"}],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["provider_payload"]["attachment_id"], "300350")

    def test_search_returns_requested_languages_across_threads(self):
        provider = self.mod.SubCentralProvider()
        board_html = b"\n".join(
            [
                _thread_link("11111", "Blue Lights - Staffel 1 - [DE-Subs: 01 | Aired: 01/06]"),
                _thread_link("22222", "Blue Lights - Staffel 1 - [VO-Subs: 01 | Aired: 01/06]"),
            ]
        )
        responses = {
            "https://www.subcentral.de/": HOME_HTML,
            "https://www.subcentral.de/index.php?page=Board&boardID=1253": board_html,
            "https://www.subcentral.de/index.php?page=Thread&threadID=11111": _visible_attachment("de", "300350"),
            "https://www.subcentral.de/index.php?page=Thread&threadID=22222": _visible_attachment("uk", "300168", "b" * 40),
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1},
            [{"alpha3": "deu", "alpha2": "de"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(
            {item["language"]["alpha3"] for item in results},
            {"deu", "eng"},
        )

    def test_search_treats_missing_episode_as_no_results(self):
        provider = self.mod.SubCentralProvider()
        responses = {
            "https://www.subcentral.de/": HOME_HTML,
            "https://www.subcentral.de/index.php?page=Board&boardID=1253": BOARD_HTML,
            "https://www.subcentral.de/index.php?page=Thread&threadID=52021": THREAD_HTML + b"\n" + THANK_XML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1},
            [{"alpha3": "deu", "alpha2": "de"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_search_reveals_guest_thank_gate_and_returns_requested_language(self):
        provider = self.mod.SubCentralProvider()
        responses = {
            "https://www.subcentral.de/": HOME_HTML,
            "https://www.subcentral.de/index.php?page=Board&boardID=1253": BOARD_HTML,
            "https://www.subcentral.de/index.php?page=Thread&threadID=52021": THREAD_HTML,
            "https://www.subcentral.de/index.php?action=Thank&output=xml&postID=568110&t=security-token&s=session-cookie-hash": THANK_XML,
        }
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1},
            [{"alpha3": "deu", "alpha2": "de"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item[0] for item in called], list(responses))
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertEqual(first["provider"], "subcentral")
        self.assertEqual(first["language"]["alpha3"], "deu")
        self.assertIn("series", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertEqual(first["provider_payload"]["attachment_id"], "300350")
        self.assertEqual(first["provider_payload"]["thread_url"], "https://www.subcentral.de/index.php?page=Thread&threadID=52021")

    def test_search_uses_attachments_already_visible_on_thread_page(self):
        provider = self.mod.SubCentralProvider()
        responses = {
            "https://www.subcentral.de/": HOME_HTML,
            "https://www.subcentral.de/index.php?page=Board&boardID=1253": BOARD_HTML,
            "https://www.subcentral.de/index.php?page=Thread&threadID=52021": THREAD_HTML + b"\n" + THANK_XML,
        }
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1},
            [{"alpha3": "deu", "alpha2": "de"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item[0] for item in called], list(responses))
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["provider_payload"]["attachment_id"], "300350")


def _zip_body(names_to_data):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in names_to_data.items():
            archive.writestr(name, data)
    return buffer.getvalue()


PAYLOAD = {
    "provider": "subcentral",
    "schema": 1,
    "attachment_id": "300350",
    "url": "https://www.subcentral.de/index.php?page=Attachment&attachmentID=300350&h=c7acdf6f69ede53d5a332e3e59798b44d18395e3",
    "thread_url": "https://www.subcentral.de/index.php?page=Thread&threadID=52021",
    "filename": "Blue.Lights.S01E01.WEBRip.x264-ION10.de-SubCentral.rar",
    "season": 1,
    "episode": 1,
}


class SubCentralDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_rar_archive_payload_unmodified(self):
        provider = self.mod.SubCentralProvider()
        provider._http_get = lambda url, timeout=15, referer=None: RAR_BODY

        result = provider.download(PAYLOAD, {"alpha3": "deu", "alpha2": "de"}, {})

        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), RAR_BODY)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(RAR_BODY).hexdigest())
        self.assertEqual(result["episode"], 1)

    def test_download_returns_zip_archive_payload_with_episode(self):
        provider = self.mod.SubCentralProvider()
        zip_body = _zip_body(
            {"Blue.Lights.S01E01.WEBRip.x264-ION10.de-SubCentral.srt": b"1\nsubtitle\n"}
        )
        provider._http_get = lambda url, timeout=15, referer=None: zip_body

        result = provider.download(PAYLOAD, {"alpha3": "deu", "alpha2": "de"}, {})

        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(zip_body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("encoding", result)

    def test_download_archive_episode_is_none_when_missing(self):
        provider = self.mod.SubCentralProvider()
        provider._http_get = lambda url, timeout=15, referer=None: RAR_BODY
        payload = dict(PAYLOAD)
        payload.pop("episode")

        result = provider.download(payload, {"alpha3": "deu", "alpha2": "de"}, {})

        self.assertEqual(base64.b64decode(result["archive_b64"]), RAR_BODY)
        self.assertIsNone(result["episode"])

    def test_download_returns_content_for_direct_subtitle_body(self):
        provider = self.mod.SubCentralProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHallo\n"
        provider._http_get = lambda url, timeout=15, referer=None: body
        payload = dict(PAYLOAD)
        payload["filename"] = "Blue.Lights.S01E01.de-SubCentral.srt"

        result = provider.download(payload, {"alpha3": "deu", "alpha2": "de"}, {})

        self.assertNotIn("archive_b64", result)
        self.assertNotIn("encoding", result)
        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertFalse(result["empty"])

    def test_download_rejects_empty_body(self):
        provider = self.mod.SubCentralProvider()
        provider._http_get = lambda url, timeout=15, referer=None: b""

        result = provider.download(PAYLOAD, {"alpha3": "deu", "alpha2": "de"}, {})

        self.assertNotIn("archive_b64", result)
        self.assertTrue(result["empty"])
        self.assertEqual(result["content_b64"], "")

    def test_download_rejects_html_error_body(self):
        provider = self.mod.SubCentralProvider()
        provider._http_get = (
            lambda url, timeout=15, referer=None: b"<!DOCTYPE html><html><body>Login required</body></html>"
        )

        result = provider.download(PAYLOAD, {"alpha3": "deu", "alpha2": "de"}, {})

        self.assertNotIn("archive_b64", result)
        self.assertTrue(result["empty"])

    def test_download_requires_url(self):
        provider = self.mod.SubCentralProvider()
        with self.assertRaises(ValueError):
            provider.download({"provider": "subcentral"}, {"alpha3": "deu"}, {})


if __name__ == "__main__":
    unittest.main()
