import json
import sys
import unittest
from pathlib import Path

from sdk import released_hosts

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import snapshot_released_host  # noqa: E402


def smoke_manifest():
    return json.loads((ROOT / "providers/smoke/provider.json").read_text(encoding="utf-8"))


class VendoredCopyTests(unittest.TestCase):
    def test_copies_match_their_recorded_git_blobs(self):
        for name in released_hosts.RELEASED_HOSTS:
            environment = released_hosts.load_host(name).environment
            for filename, blob in environment["vendored_blobs"].items():
                with self.subTest(host=name, file=filename):
                    data = (released_hosts.HOSTS_DIR / name / filename).read_bytes()
                    self.assertEqual(released_hosts.git_blob_id(data), blob)

    def test_snapshots_pin_the_agreed_commits(self):
        self.assertEqual(
            released_hosts.load_host("bazarr_v2_6_2").environment["bazarr_commit"],
            "6c354d646dbb11852bd4cdec667cbbfb8170782f",
        )
        for name, ref in (("bazarr_v2_6_2", "v2.6.2"), ("bazarr_v2_7_0", "v2.7.0"), ("bazarr_v2_7_2", "v2.7.2")):
            self.assertEqual(released_hosts.load_host(name).environment["bazarr_ref"], ref)

    def test_2_7_2_snapshot_carries_the_official_id_reservation(self):
        migration = released_hosts.load_host("bazarr_v2_7_2").migration
        self.assertIn("subdl", migration.OFFICIAL_CATALOG_PROVIDER_IDS)
        self.assertFalse(hasattr(released_hosts.load_host("bazarr_v2_7_0").migration, "OFFICIAL_CATALOG_PROVIDER_IDS"))

    def test_every_snapshot_directory_is_registered(self):
        directories = sorted(
            path.name for path in released_hosts.HOSTS_DIR.iterdir() if (path / "environment.json").is_file()
        )
        self.assertEqual(directories, sorted(released_hosts.RELEASED_HOSTS))

    def test_environments_are_not_empty(self):
        for name in released_hosts.RELEASED_HOSTS:
            environment = released_hosts.load_host(name).environment
            self.assertGreater(len(environment["built_in_provider_modules"]), 40)
            self.assertIn("sonarr", environment["settings_sections"])


class ReleasedValidatorTests(unittest.TestCase):
    def test_every_checked_in_provider_passes_every_released_host(self):
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        manifests = [item["manifest"] for item in catalog["providers"]]
        self.assertEqual(released_hosts.released_host_errors(manifests), [])

    def test_settings_section_id_is_refused_by_the_2_7_validator(self):
        manifest = smoke_manifest()
        manifest["provider_id"] = "sonarr"
        errors = released_hosts.released_host_errors([manifest])
        self.assertEqual(len(errors), 2)
        self.assertIn("bazarr_v2_7_0 refuses sonarr", errors[0])
        self.assertIn("bazarr_v2_7_2 refuses sonarr", errors[1])
        self.assertIn("reserved by a Bazarr settings section", errors[0])

    def test_new_host_features_are_refused_by_both(self):
        manifest = smoke_manifest()
        manifest["schema_version"] = 2
        errors = released_hosts.released_host_errors([manifest])
        self.assertEqual(len(errors), 3)
        manifest = smoke_manifest()
        manifest["files"] = dict(manifest["files"], **{"data.json": "a" * 64})
        self.assertEqual(len(released_hosts.released_host_errors([manifest])), 3)

    def test_malformed_entry_is_reported_not_raised(self):
        manifest = smoke_manifest()
        manifest["files"] = ["provider.py"]
        errors = released_hosts.released_host_errors([manifest, "not a manifest"])
        self.assertEqual(len(errors), 6)
        self.assertTrue(all(" refuses " in line for line in errors))

    def test_untrusted_shadowing_rule_is_the_hosts(self):
        manifest = smoke_manifest()
        manifest["provider_id"] = "addic7ed"  # migrated built-in, shadowable by a trusted entry
        self.assertEqual(released_hosts.released_host_errors([manifest]), [])

    def test_reserved_sections_exclude_provider_sections(self):
        reserved = released_hosts.reserved_settings_sections()
        self.assertIn("sonarr", reserved)
        self.assertIn("translator", reserved)
        self.assertNotIn("subdl", reserved)
        self.assertIn("podnapisi", released_hosts.all_built_in_provider_ids())


class SnapshotScriptTests(unittest.TestCase):
    def test_extract_settings_sections(self):
        source = (
            "validators = [\n"
            "    Validator('general.ip', default='*'),\n"
            "    Validator('Sonarr.apikey'),\n"
            "    Validator(f'dynamic.{x}'),\n"
            "]\n"
            "other = [Validator('ignored.key')]\n"
        )
        self.assertEqual(snapshot_released_host.extract_settings_sections(source), ["general", "sonarr"])

    def test_registers_provider(self):
        self.assertTrue(snapshot_released_host.registers_provider("class ExampleProvider(Provider):\n    pass\n"))
        self.assertTrue(snapshot_released_host.registers_provider("from .avistaz_network import AvistazNetworkProvider\n"))
        self.assertFalse(snapshot_released_host.registers_provider("class Provider:\n    pass\nclass _HiddenProvider:\n    pass\n"))
        self.assertFalse(snapshot_released_host.registers_provider("AGENTS = ['x']\n"))

    def test_copied_file_importing_outside_the_standard_library_is_refused(self):
        for source in ("from app.config import validators\n", "import subliminal_patch\n", "def load():\n    import app\n"):
            with self.subTest(source=source):
                with self.assertRaises(SystemExit):
                    snapshot_released_host.refuse_outside_imports("manifest.py", source.encode("utf-8"))

    def test_standard_library_and_relative_imports_are_allowed(self):
        source = b"from __future__ import annotations\nimport re\nfrom . import PROVIDER_HUB_API_VERSION\nfrom .registry import NAME\n"
        snapshot_released_host.refuse_outside_imports("manifest.py", source)

    def test_vendored_copies_import_only_the_standard_library_and_their_package(self):
        for path in released_hosts.HOSTS_DIR.glob("*/*.py"):
            with self.subTest(file=str(path)):
                snapshot_released_host.refuse_outside_imports(path.name, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
