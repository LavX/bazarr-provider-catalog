import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sdk import cli

ROOT = Path(__file__).resolve().parents[1]


class ProvidersFeedTests(unittest.TestCase):
    def make_root(self, **manifest_changes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        shutil.copytree(ROOT / "providers/smoke", root / "providers/smoke", ignore=shutil.ignore_patterns("__pycache__"))
        manifest_path = root / "providers/smoke/provider.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(manifest_changes)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return root

    def test_validate_runs_the_released_host_validators(self):
        root = self.make_root(provider_id="sonarr")
        catalog = cli.build_catalog(root, write_manifests=True)
        (root / "catalog.json").write_text(cli.dump_json(catalog), encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "bazarr_v2_7_0 refuses sonarr: provider_id 'sonarr' is reserved"):
            cli.validate_catalog(root)

    def test_schema_2_fields_are_refused_in_providers(self):
        for field, value in (("requires", ["flaresolverr.v1"]), ("endpoints", ["https://api.example.org:443"]), ("kind", "subtitle_provider")):
            with self.subTest(field=field):
                root = self.make_root(**{field: value})
                with self.assertRaisesRegex(cli.CatalogError, rf"uses \['{field}'\], which released hosts ignore"):
                    cli.build_catalog(root)


if __name__ == "__main__":
    unittest.main()
