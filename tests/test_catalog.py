import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from unittest import mock
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

    def test_py7zz_providers_pin_requests_dependency_closure(self):
        required = {"certifi", "charset-normalizer", "idna", "requests", "urllib3"}

        for manifest_path in sorted((ROOT / "providers").glob("*/provider.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            dependencies = {
                item["name"]
                for item in manifest.get("dependencies", {}).get("requirements", [])
            }
            if "py7zz" not in dependencies:
                continue
            missing = sorted(required - dependencies)
            self.assertEqual(missing, [], f"{manifest_path} is missing py7zz transitive pins")


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
        self.assertEqual(matrix["abi_tags"], ["cp312", "cp313", "cp314", "abi3"])
        self.assertIn("cp311-abi3", matrix["wheel_coverage"])
        self.assertIn("py3-none-any", matrix["wheel_coverage"])
        self.assertNotIn("3.11", result.stdout)

    def test_validate_rejects_underhashed_multiwheel_dependency(self):
        from sdk import cli as sdk_cli

        single = {"requirements": [{"name": "cffi", "version": "2.0.0", "hashes": ["sha256:" + "a" * 64]}]}
        with self.assertRaisesRegex(sdk_cli.CatalogError, "cffi"):
            sdk_cli.validate_dependency_lock("manifest.json", single)

        multi = {
            "requirements": [
                {"name": "cffi", "version": "2.0.0", "hashes": ["sha256:" + "a" * 64, "sha256:" + "b" * 64]}
            ]
        }
        sdk_cli.validate_dependency_lock("manifest.json", multi)  # must not raise

    def test_validate_rejects_bundled_archive_library(self):
        from sdk import cli as sdk_cli

        # Archive extraction is host-side now, so a worker must never bundle py7zz,
        # py7zr, or rarfile. The host extracts zip/rar/7z from an archive_b64 payload.
        for name in ("py7zz", "py7zr", "rarfile"):
            lock = {"requirements": [{"name": name, "version": "1.0.0", "hashes": ["sha256:" + "c" * 64]}]}
            with self.assertRaisesRegex(sdk_cli.CatalogError, name):
                sdk_cli.validate_dependency_lock("manifest.json", lock)

    def test_validate_rejects_bundled_archive_library_regardless_of_casing(self):
        from sdk import cli as sdk_cli

        # The ban compares PEP 503 normalized names, so a different casing (Py7zz, RarFile)
        # must not slip a banned archive library past the gate.
        for name in ("Py7zz", "PY7ZR", "RarFile", "PY7zz", "RARFILE"):
            lock = {"requirements": [{"name": name, "version": "1.0.0", "hashes": ["sha256:" + "c" * 64]}]}
            with self.assertRaises(sdk_cli.CatalogError):
                sdk_cli.validate_dependency_lock("manifest.json", lock)

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

    def test_smoke_test_accepts_normalized_regional_language_payload(self):
        from sdk import cli as sdk_cli

        class RegionalProvider:
            def search(self, video, languages, config):
                del video, languages, config
                return [
                    {
                        "provider": "regional",
                        "language": {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR", "hi": False, "forced": False},
                        "provider_payload": {"download_link": "fixture"},
                    }
                ]

        catalog = {
            "providers": [
                {
                    "manifest": {
                        "provider_id": "regional",
                        "source": {"path": "providers/regional"},
                        "secret_fields": [],
                    }
                }
            ]
        }
        languages = [{"alpha3": "por-BR", "hi": False, "forced": False}]

        with mock.patch.object(sdk_cli, "validate_catalog", return_value=catalog), mock.patch.object(
            sdk_cli, "load_provider_class", return_value=RegionalProvider
        ):
            provider_id = sdk_cli.smoke_test(ROOT, "regional", videos=[{}], languages=languages, skip_download=True)

        self.assertEqual(provider_id, "regional")


class ArchiveDownloadValidationTests(unittest.TestCase):
    def _zip(self, names):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        return buffer.getvalue()

    def test_archive_download_accepts_zip_with_named_member(self):
        from sdk import cli as sdk_cli

        raw = self._zip(["Show.S01E01.srt", "readme.txt"])
        sdk_cli._validate_archive_download(
            "x",
            {
                "archive_b64": base64.b64encode(raw).decode("ascii"),
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "member": "Show.S01E01.srt",
            },
        )  # must not raise

    def test_archive_download_rejects_missing_member(self):
        from sdk import cli as sdk_cli

        raw = self._zip(["Show.S01E01.srt"])
        with self.assertRaises(sdk_cli.CatalogError):
            sdk_cli._validate_archive_download(
                "x", {"archive_b64": base64.b64encode(raw).decode("ascii"), "member": "missing.srt"}
            )

    def test_archive_download_rejects_archive_without_subtitle(self):
        from sdk import cli as sdk_cli

        raw = self._zip(["readme.txt", "cover.jpg"])
        with self.assertRaises(sdk_cli.CatalogError):
            sdk_cli._validate_archive_download(
                "x", {"archive_b64": base64.b64encode(raw).decode("ascii")}
            )

    def test_archive_download_rejects_sha256_mismatch(self):
        from sdk import cli as sdk_cli

        raw = self._zip(["a.srt"])
        with self.assertRaises(sdk_cli.CatalogError):
            sdk_cli._validate_archive_download(
                "x", {"archive_b64": base64.b64encode(raw).decode("ascii"), "archive_sha256": "0" * 64}
            )

    def test_archive_download_rejects_non_archive_payload(self):
        from sdk import cli as sdk_cli

        # A non-empty, non-zip, non-rar payload (e.g. an HTML error page or bare subtitle
        # bytes accidentally wrapped in archive_b64) must be rejected, not silently passed.
        for raw in (
            b"<html><body>error</body></html>",
            b"1\n00:00:01,000 --> 00:00:02,000\nDirect subtitle\n",
        ):
            with self.assertRaises(sdk_cli.CatalogError):
                sdk_cli._validate_archive_download(
                    "x", {"archive_b64": base64.b64encode(raw).decode("ascii")}
                )

    def test_archive_download_accepts_rar_without_listing(self):
        from sdk import cli as sdk_cli

        # RAR cannot be listed with the stdlib, so the offline validator accepts the bytes
        # (the host extracts) as long as they carry the rar signature.
        raw = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        sdk_cli._validate_archive_download(
            "x",
            {
                "archive_b64": base64.b64encode(raw).decode("ascii"),
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )  # must not raise

    def test_archive_download_accepts_7z_without_listing(self):
        from sdk import cli as sdk_cli

        # 7z is also a host-supported archive format (many providers hand back .7z bodies);
        # it is not stdlib-listable, so the validator accepts the signed bytes.
        raw = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32
        sdk_cli._validate_archive_download(
            "x",
            {
                "archive_b64": base64.b64encode(raw).decode("ascii"),
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )  # must not raise


if __name__ == "__main__":
    unittest.main()
