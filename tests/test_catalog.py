import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SRT = """1
00:00:01,000 --> 00:00:02,500
SmokeHub deterministic subtitle.
"""


class CatalogStructureTests(unittest.TestCase):
    def test_catalog_embeds_smokehub_manifest(self):
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(len(catalog["providers"]), 1)
        manifest = catalog["providers"][0]["manifest"]
        self.assertEqual(manifest["provider_id"], "smokehub")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["source"]["path"], "providers/smoke")

    def test_smoke_manifest_matches_provider_hub_v1_contract(self):
        manifest = json.loads((ROOT / "providers/smoke/provider.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["provider_id"], "smokehub")
        self.assertEqual(manifest["api_version"], "bazarr.provider-hub.v1")
        self.assertEqual(manifest["entry_module"], "provider")
        self.assertEqual(manifest["entry_class"], "SmokeProvider")
        self.assertEqual(manifest["supported_media"], ["movie", "episode"])
        self.assertEqual(manifest["languages"], ["eng"])
        self.assertEqual(manifest["dependencies"], {"requirements": []})
        self.assertRegex(manifest["files"]["provider.py"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["bundle_sha256"], r"^[0-9a-f]{64}$")

    def test_catalog_entry_has_fields_bazarr_refresh_uses(self):
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        entry = catalog["providers"][0]
        manifest = entry["manifest"]

        self.assertTrue(manifest["provider_id"])
        self.assertTrue(manifest["version"])


class SmokeProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        provider_path = ROOT / "providers/smoke/provider.py"
        spec = importlib.util.spec_from_file_location("smoke_provider", provider_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.provider = module.SmokeProvider()

    def test_search_returns_one_worker_candidate(self):
        results = self.provider.search(
            video={"kind": "movie", "title": "Any Movie"},
            languages=[{"alpha3": "eng", "hi": False, "forced": False}],
            config={},
        )

        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["id"], "smokehub-fixed-eng")
        self.assertEqual(candidate["provider"], "smokehub")
        self.assertEqual(candidate["language"]["alpha3"], "eng")
        self.assertIn("title", candidate["matches"])
        self.assertEqual(candidate["provider_payload"]["subtitle_id"], "smokehub-fixed-eng")

    def test_search_rejects_unsupported_media_or_language(self):
        self.assertEqual(self.provider.search(video={"kind": "movie"}, languages=[{"alpha3": "fra"}], config={}), [])
        self.assertEqual(self.provider.search(video={"kind": "series"}, languages=[{"alpha3": "eng"}], config={}), [])

    def test_download_returns_fixed_utf8_srt_payload(self):
        content = self.provider.download({"subtitle_id": "smokehub-fixed-eng"}, {"alpha3": "eng"}, {})
        data = base64.b64decode(content["content_b64"].encode("ascii"), validate=True)

        self.assertFalse(content["empty"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), content["content_sha256"])
        self.assertEqual(data.decode("utf-8"), EXPECTED_SRT)


class SdkCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "sdk", *args],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_hash_outputs_provider_digest(self):
        result = self.run_cli("hash", "providers/smoke")

        self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{64}$")

    def test_validate_accepts_catalog(self):
        result = self.run_cli("validate")

        self.assertIn("catalog ok", result.stdout)

    def test_build_catalog_matches_checked_in_catalog(self):
        result = self.run_cli("build-catalog", "--stdout")
        generated = json.loads(result.stdout)
        checked_in = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

        self.assertEqual(generated, checked_in)

    def test_smoke_test_runs_worker_shape_contract(self):
        result = self.run_cli("smoke-test")

        self.assertIn("smokehub ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
