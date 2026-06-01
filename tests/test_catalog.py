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
SMOKE_CONFIG = {"profile_name": "smoke-profile", "api_token": "smoke-secret-token"}


class CatalogStructureTests(unittest.TestCase):
    def test_catalog_embeds_smokehub_manifest(self):
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

        self.assertEqual(catalog["schema_version"], 1)
        provider_ids = {item["manifest"]["provider_id"] for item in catalog["providers"]}
        self.assertIn("smokehub", provider_ids)
        self.assertIn("subtitlecat", provider_ids)
        manifest = next(item["manifest"] for item in catalog["providers"] if item["manifest"]["provider_id"] == "smokehub")
        self.assertEqual(manifest["provider_id"], "smokehub")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["source"]["path"], "providers/smoke")

    def test_smoke_manifest_matches_provider_hub_v1_contract(self):
        manifest = json.loads((ROOT / "providers/smoke/provider.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["provider_id"], "smokehub")
        self.assertEqual(manifest["api_version"], "bazarr.provider-hub.v1")
        self.assertEqual(manifest["entry_module"], "provider")
        self.assertEqual(manifest["entry_class"], "SmokeProvider")
        self.assertEqual(manifest["supported_media"], ["movie", "episode"])
        self.assertEqual(manifest["languages"], ["eng"])
        self.assertEqual(
            manifest["dependencies"]["requirements"],
            [
                {
                    "name": "humanfriendly",
                    "version": "10.0",
                    "hashes": [
                        "sha256:1697e1a8a8f550fd43c2865cd84542fc175a61dcb779b6fee18cf6b6ccba1477"
                    ],
                }
            ],
        )
        self.assertEqual(manifest["config_schema"]["required"], ["profile_name", "api_token"])
        self.assertEqual(manifest["secret_fields"], ["api_token"])
        self.assertTrue(manifest["config_schema"]["properties"]["api_token"]["secret"])
        self.assertRegex(manifest["files"]["provider.py"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["bundle_sha256"], r"^[0-9a-f]{64}$")

    def test_catalog_entry_has_fields_bazarr_refresh_uses(self):
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        entry = catalog["providers"][0]
        manifest = entry["manifest"]

        self.assertTrue(manifest["provider_id"])
        self.assertTrue(manifest["version"])


@unittest.skipUnless(importlib.util.find_spec("humanfriendly"), "requires smokehub provider dependencies")
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
            config=SMOKE_CONFIG,
        )

        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["id"], "smokehub-fixed-eng")
        self.assertEqual(candidate["provider"], "smokehub")
        self.assertEqual(candidate["language"]["alpha3"], "eng")
        self.assertIn("title", candidate["matches"])
        self.assertEqual(candidate["provider_payload"]["subtitle_id"], "smokehub-fixed-eng")
        self.assertEqual(candidate["provider_payload"]["profile_name"], "smoke-profile")
        self.assertNotIn("smoke-secret-token", json.dumps(candidate, sort_keys=True))

    def test_search_rejects_unsupported_media_or_language(self):
        self.assertEqual(self.provider.search(video={"kind": "movie"}, languages=[{"alpha3": "fra"}], config=SMOKE_CONFIG), [])
        self.assertEqual(self.provider.search(video={"kind": "series"}, languages=[{"alpha3": "eng"}], config=SMOKE_CONFIG), [])

    def test_search_requires_secret_config(self):
        with self.assertRaisesRegex(ValueError, "api_token"):
            self.provider.search(
                video={"kind": "movie"},
                languages=[{"alpha3": "eng"}],
                config={"profile_name": "smoke-profile"},
            )

    def test_download_returns_fixed_utf8_srt_payload(self):
        content = self.provider.download(
            {"subtitle_id": "smokehub-fixed-eng", "profile_name": "smoke-profile"},
            {"alpha3": "eng"},
            SMOKE_CONFIG,
        )
        data = base64.b64decode(content["content_b64"].encode("ascii"), validate=True)

        self.assertFalse(content["empty"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), content["content_sha256"])
        self.assertEqual(data.decode("utf-8"), EXPECTED_SRT)
        self.assertNotIn("smoke-secret-token", json.dumps(content, sort_keys=True))


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

    def test_runtime_matrix_reports_bazarr_plus_python_versions(self):
        result = self.run_cli("runtime-matrix")
        matrix = json.loads(result.stdout)

        self.assertEqual(matrix["python_requires"], ">=3.12,<3.15")
        self.assertEqual(matrix["python_versions"], ["3.12", "3.13", "3.14"])
        self.assertEqual(matrix["abi_tags"], ["cp312", "cp313", "cp314"])
        self.assertIn("py3-none-any", matrix["wheel_coverage"])
        self.assertNotIn("3.11", result.stdout)

    def test_readme_python_badge_matches_runtime_matrix(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Python-3.12%20to%203.14", readme)
        self.assertNotIn("Python-3.11%2B", readme)

    def test_build_catalog_matches_checked_in_catalog(self):
        result = self.run_cli("build-catalog", "--stdout")
        generated = json.loads(result.stdout)
        checked_in = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

        self.assertEqual(generated, checked_in)

    @unittest.skipUnless(importlib.util.find_spec("humanfriendly"), "requires smokehub provider dependencies")
    def test_smoke_test_runs_worker_shape_contract(self):
        result = self.run_cli("smoke-test")

        self.assertIn("smokehub ok", result.stdout)

    @unittest.skipUnless(importlib.util.find_spec("humanfriendly"), "requires smokehub provider dependencies")
    def test_smoke_test_accepts_config_json(self):
        result = self.run_cli(
            "smoke-test",
            "--config-json",
            json.dumps({"profile_name": "cli-profile", "api_token": "cli-secret"}),
        )

        self.assertIn("smokehub ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
