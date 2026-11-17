import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sdk import cli

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "fixture_translator"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "plugins" / FIXTURE
IGNORE = shutil.ignore_patterns("__pycache__")


class PluginFeedTestCase(unittest.TestCase):
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        shutil.copytree(ROOT / "providers/smoke", root / "providers/smoke", ignore=IGNORE)
        shutil.copytree(FIXTURE_DIR, root / "plugins" / FIXTURE, ignore=IGNORE)
        return root

    def provider_ids(self, root):
        return cli.provider_ids(cli.build_catalog(root))

    def build_plugins(self, root):
        return cli.build_plugins(root, taken_ids=self.provider_ids(root))

    def refused(self, changes, pattern):
        root = self.make_root()
        path = root / "plugins" / FIXTURE / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if value is None:
                manifest.pop(key, None)
            else:
                manifest[key] = value
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, pattern):
            cli.build_plugins(root, taken_ids=self.provider_ids(root), write_manifests=True)


class PluginManifestTests(PluginFeedTestCase):
    def test_the_checked_in_catalog_validates(self):
        cli.validate_catalog(ROOT)

    def test_the_fixture_plugin_is_valid(self):
        self.assertTrue((FIXTURE_DIR / "plugin.json").is_file())
        self.assertEqual([item["id"] for item in self.build_plugins(self.make_root())], [FIXTURE])

    def test_a_valid_translator_manifest_is_accepted(self):
        manifests = self.build_plugins(self.make_root())
        self.assertEqual([item["id"] for item in manifests], [FIXTURE])
        self.assertEqual(manifests[0]["kind_api"], "bazarr.translator.v1")

    def test_a_plugin_with_no_optional_fields_is_accepted(self):
        optional = (
            "min_host_version", "runtime", "platforms", "permissions", "endpoints", "provides",
            "requires", "settings", "settings_version", "description", "python_tags",
        )
        root = self.make_root()
        path = root / "plugins" / FIXTURE / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for key in optional:
            manifest.pop(key, None)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        cli.build_plugins(root, write_manifests=True)
        self.build_plugins(root)

    def test_every_envelope_refusal(self):
        cases = {
            "schema 1": ({"schema_version": 1}, "schema_version must be 2"),
            "missing id": ({"id": None}, "id must be a non-empty string"),
            "bad id": ({"id": "Fixture-Translator"}, "id must use lowercase plugin id syntax"),
            "id differs from provider_id": ({"provider_id": "other_one"}, "provider_id, when present, must equal id"),
            "missing kind": ({"kind": None}, "kind must be a non-empty string"),
            "bad kind": ({"kind": "Translator"}, "kind must use lowercase kind syntax"),
            "subtitle provider kind": ({"kind": "subtitle_provider", "kind_api": "bazarr.subtitle_provider.v1"}, "belong under providers/"),
            "bad kind_api": ({"kind_api": "translator.v1"}, "kind_api must look like"),
            "kind_api of another kind": ({"kind_api": "bazarr.other.v1"}, "does not belong to kind translator"),
            "kind_api major zero": ({"kind_api": "bazarr.translator.v0"}, "kind_api must look like"),
            "empty name": ({"name": " "}, "name must be a non-empty string"),
            "unsafe version": ({"version": "../1"}, "version must be a safe identifier"),
            "min_host_version not semver": ({"min_host_version": "2.8"}, "min_host_version must be a semantic version"),
            "runtime not an object": ({"runtime": "python"}, "runtime must be an object"),
            "unsupported runtime pair": ({"runtime": {"artifact": "python", "process": "daemon"}}, "is not a supported combination"),
            "archive runtime on any platform": ({"runtime": {"artifact": "archive", "process": "worker"}}, "must name the platforms it is built for"),
            "oci runtime without docker": ({"runtime": {"artifact": "oci", "process": "daemon"}}, "permissions must include docker"),
            "unknown permission": ({"permissions": ["root"]}, "unknown permission: root"),
            "repeated permission": ({"permissions": ["network", "network"]}, "must not repeat a value"),
            "unknown platform": ({"platforms": ["beos"]}, "unknown platform: beos"),
            "empty platforms": ({"platforms": []}, "platforms must not be empty"),
            "any with a named platform": ({"platforms": ["any", "linux-x86_64"]}, "'any' together with specific platforms"),
            "endpoint without a port": ({"endpoints": ["https://translate.example.org"]}, "scheme://host:port"),
            "endpoint with a path": ({"endpoints": ["https://translate.example.org:443/v1"]}, "scheme://host:port"),
            "endpoint port out of range": ({"endpoints": ["https://translate.example.org:99999"]}, "scheme://host:port"),
            "capability not versioned": ({"provides": ["translator.engine"]}, "must look like name.v1"),
            "host capability provided": ({"provides": ["host.storage.v1"]}, "cannot provide a host capability"),
            "requires not a list": ({"requires": "x.v1"}, "requires must be a list of strings"),
            "extends not an object": ({"extends": "other"}, "extends must be an object"),
            "extends itself": ({"extends": {"id": FIXTURE, "capability": "ai_translator.backend.v1"}}, "cannot extend itself"),
            "extends a host capability": ({"extends": {"id": "other_one", "capability": "host.x.v1"}}, "extends.capability must be a plugin capability"),
            "python tag": ({"python_tags": ["cp3"]}, "python_tags entry 'cp3'"),
            "settings_version without settings": ({"settings": None}, "settings_version is set but there are no settings"),
            "settings without settings_version": ({"settings_version": None}, "settings_version must be a positive integer"),
            "settings field with an unknown type": ({"settings": {"fields": [{"key": "a", "type": "blob"}]}}, "unsupported type"),
            "settings field declared twice": ({"settings": {"fields": [{"key": "a", "type": "text"}, {"key": "a", "type": "text"}]}}, "declared twice"),
            "secret flag on a toggle": ({"settings": {"fields": [{"key": "a", "type": "toggle", "secret": True}]}}, "marked secret"),
            "backend host field as a secret": (
                {"settings": {"fields": [], "backend_types": {"x": {"fields": [{"key": "model", "type": "secret"}]}}}},
                "kept by the host as a plain value",
            ),
            "login without the permission": (
                {"login": {"argv": ["tool", "login"], "flow": "device_code", "paths": ["auth.json"]}},
                "login needs the interactive_login permission",
            ),
            "login with an unknown flow": (
                {"permissions": ["interactive_login"], "login": {"argv": ["tool"], "flow": "password", "paths": ["a"]}},
                "login.flow must be one of",
            ),
        }
        for label, (changes, pattern) in cases.items():
            with self.subTest(label):
                self.refused(changes, pattern)

    def test_bundle_field_refusals(self):
        cases = {
            "wrong source repo": ({"source": {"type": "github", "repo": "someone/else", "ref": "main",
                                              "commit": "0" * 40, "path": f"plugins/{FIXTURE}"}}, "source.repo must be"),
            "wrong source ref": ({"source": {"type": "github", "repo": "LavX/bazarr-provider-catalog", "ref": "dev",
                                             "commit": "0" * 40, "path": f"plugins/{FIXTURE}"}}, "source.ref must be main"),
            "short commit": ({"source": {"type": "github", "repo": "LavX/bazarr-provider-catalog", "ref": "main",
                                         "commit": "abc", "path": f"plugins/{FIXTURE}"}}, "source.commit must be"),
            "wrong source path": ({"source": {"type": "github", "repo": "LavX/bazarr-provider-catalog", "ref": "main",
                                              "commit": "0" * 40, "path": "plugins/other"}}, f"source.path must be plugins/{FIXTURE}"),
            "no source": ({"source": None}, "source.type must be github"),
            "dependency without hashes": ({"dependencies": {"requirements": [{"name": "pkg", "version": "1.0"}]}}, "needs hashes"),
            "dependency not pinned": (
                {"dependencies": {"requirements": [{"name": "pkg", "version": ">=1.0", "hashes": ["sha256:" + "a" * 64]}]}},
                "must be pinned",
            ),
            "dependency with a bad hash": (
                {"dependencies": {"requirements": [{"name": "pkg", "version": "1.0", "hashes": ["sha256:xyz"]}]}},
                "invalid hash",
            ),
            "ABI specific wheel with one hash": (
                {"dependencies": {"requirements": [{"name": "cryptography", "version": "46.0.0", "hashes": ["sha256:" + "a" * 64]}]}},
                "ships ABI-specific wheels",
            ),
        }
        for label, (changes, pattern) in cases.items():
            with self.subTest(label):
                self.refused(changes, pattern)

    def test_a_dependency_with_a_hash_per_wheel_is_accepted(self):
        requirement = {"name": "cryptography", "version": "46.0.0",
                       "hashes": ["sha256:" + "a" * 64, "sha256:" + "b" * 64, "sha256:" + "c" * 64]}
        root = self.make_root()
        path = root / "plugins" / FIXTURE / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["dependencies"] = {"requirements": [requirement]}
        path.write_text(json.dumps(manifest), encoding="utf-8")
        cli.build_plugins(root, write_manifests=True)
        self.build_plugins(root)

    def test_a_stale_file_hash_is_refused(self):
        root = self.make_root()
        (root / "plugins" / FIXTURE / "translator.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "sha mismatch for translator.py"):
            self.build_plugins(root)

    def test_a_stale_bundle_hash_is_refused(self):
        root = self.make_root()
        path = root / "plugins" / FIXTURE / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["bundle_sha256"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "bundle_sha256 is stale"):
            self.build_plugins(root)

    def test_an_undeclared_file_is_refused(self):
        root = self.make_root()
        (root / "plugins" / FIXTURE / "extra.txt").write_text("hello", encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "undeclared file in plugin bundle: fixture_translator/extra.txt"):
            self.build_plugins(root)

    def test_build_catalog_declares_a_new_bundle_file(self):
        root = self.make_root()
        data = root / "plugins" / FIXTURE / "data" / "prompt.txt"
        data.parent.mkdir()
        data.write_text("hello", encoding="utf-8")
        manifests = cli.build_plugins(root, write_manifests=True)
        self.assertIn("data/prompt.txt", manifests[0]["files"])

    def test_a_missing_declared_file_is_refused(self):
        root = self.make_root()
        (root / "plugins" / FIXTURE / "translator.py").unlink()
        with self.assertRaisesRegex(cli.CatalogError, "missing declared file"):
            self.build_plugins(root)

    def test_a_bundle_cannot_carry_the_hosts_reserved_file(self):
        root = self.make_root()
        (root / "plugins" / FIXTURE / "provider.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "files must not list provider.json"):
            cli.build_plugins(root, write_manifests=True)

    def test_a_symlink_is_refused(self):
        root = self.make_root()
        os.symlink("translator.py", root / "plugins" / FIXTURE / "alias.py")
        with self.assertRaisesRegex(cli.CatalogError, "symlink not allowed"):
            cli.build_plugins(root, write_manifests=True)

    def test_the_id_must_match_the_directory(self):
        root = self.make_root()
        shutil.move(str(root / "plugins" / FIXTURE), str(root / "plugins" / "renamed"))
        with self.assertRaisesRegex(cli.CatalogError, "id must match its directory name renamed"):
            cli.build_plugins(root, write_manifests=True)

    def test_a_plugin_cannot_reuse_a_provider_id(self):
        root = self.make_root()
        provider_id = next(iter(self.provider_ids(root)))
        shutil.move(str(root / "plugins" / FIXTURE), str(root / "plugins" / provider_id))
        path = root / "plugins" / provider_id / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["id"] = provider_id
        manifest["source"]["path"] = f"plugins/{provider_id}"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, f"duplicate plugin or provider id: {provider_id}"):
            cli.build_plugins(root, taken_ids=self.provider_ids(root), write_manifests=True)

    def test_a_copied_plugin_directory_keeps_the_old_id_and_is_refused(self):
        root = self.make_root()
        shutil.copytree(root / "plugins" / FIXTURE, root / "plugins" / "twin", ignore=IGNORE)
        with self.assertRaisesRegex(cli.CatalogError, "id must match its directory name twin"):
            cli.build_plugins(root, write_manifests=True)

    def test_a_directory_without_a_manifest_is_refused(self):
        root = self.make_root()
        (root / "plugins" / "empty").mkdir()
        with self.assertRaisesRegex(cli.CatalogError, "plugins/empty has no plugin.json"):
            self.build_plugins(root)

    def test_a_stray_file_in_plugins_is_refused(self):
        root = self.make_root()
        (root / "plugins" / "notes.txt").write_text("hello", encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "unexpected entry in plugins/: notes.txt"):
            self.build_plugins(root)

    def test_validate_refuses_a_stale_plugin(self):
        root = self.make_root()
        cli.build_catalog(root, write_manifests=True)
        (root / "catalog.json").write_text(cli.dump_json(cli.build_catalog(root)), encoding="utf-8")
        cli.validate_catalog(root)
        (root / "plugins" / FIXTURE / "translator.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "sha mismatch for translator.py"):
            cli.validate_catalog(root)

    def test_a_catalog_without_plugins_still_validates(self):
        root = self.make_root()
        shutil.rmtree(root / "plugins")
        self.assertEqual(self.build_plugins(root), [])


class LegacyCatalogTests(PluginFeedTestCase):
    def test_catalog_json_is_byte_identical_to_the_checked_in_file(self):
        self.assertEqual(cli.dump_json(cli.build_catalog(ROOT)), (ROOT / "catalog.json").read_text(encoding="utf-8"))

    def test_catalog_json_does_not_change_with_plugins(self):
        with_plugins = self.make_root()
        without_plugins = self.make_root()
        shutil.rmtree(without_plugins / "plugins")
        self.assertEqual(
            cli.dump_json(cli.build_catalog(with_plugins)), cli.dump_json(cli.build_catalog(without_plugins))
        )

    def test_build_catalog_command_leaves_catalog_json_alone_when_plugins_change(self):
        root = self.make_root()
        run = lambda: subprocess.run(  # noqa: E731
            [sys.executable, "-B", "-m", "sdk", "build-catalog", str(root)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        run()
        before = (root / "catalog.json").read_bytes()
        (root / "plugins" / FIXTURE / "translator.py").write_text("x = 2\n", encoding="utf-8")
        shutil.copytree(root / "plugins" / FIXTURE, root / "plugins" / "second_one", ignore=IGNORE)
        path = root / "plugins" / "second_one" / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["id"] = "second_one"
        manifest["source"]["path"] = "plugins/second_one"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        run()
        self.assertEqual((root / "catalog.json").read_bytes(), before)
        self.assertIn(
            cli.file_sha256(root / "plugins" / FIXTURE / "translator.py"),
            (root / "plugins" / FIXTURE / "plugin.json").read_text(encoding="utf-8"),
        )

    def test_the_released_host_check_never_sees_plugins(self):
        root = self.make_root()
        cli.build_catalog(root, write_manifests=True)
        (root / "catalog.json").write_text(cli.dump_json(cli.build_catalog(root)), encoding="utf-8")
        seen = []
        real = cli.released_hosts.released_host_errors

        def record(manifests):
            seen.extend(manifests)
            return real(manifests)

        with mock.patch.object(cli.released_hosts, "released_host_errors", record):
            cli.validate_catalog(root)
        self.assertTrue(seen)
        for manifest in seen:
            self.assertNotEqual(manifest.get("schema_version"), 2)
            self.assertNotEqual(manifest.get("provider_id"), FIXTURE)

    def test_a_provider_with_schema_2_fields_is_still_refused(self):
        root = self.make_root()
        path = root / "providers/smoke/provider.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["kind"] = "subtitle_provider"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "which released hosts ignore"):
            cli.build_catalog(root)


class SignedFeedBodyTests(PluginFeedTestCase):
    def test_the_fixture_is_never_published_from_the_repo(self):
        self.assertFalse((ROOT / "plugins" / FIXTURE).exists())
        self.assertNotIn(FIXTURE, (ROOT / "catalog.json").read_text(encoding="utf-8"))
        self.assertNotIn(FIXTURE, cli.dump_json(cli.build_catalog(ROOT)))
        for channel in cli.SIGNED_FEED_CHANNELS:
            body = cli.build_signed_feed(ROOT, channel)
            self.assertNotIn(FIXTURE, cli.dump_json(body))
            self.assertNotIn(FIXTURE, [item["manifest"].get("id") for item in body["plugins"]])

    def add_plugin(self, root, plugin_id):
        shutil.copytree(root / "plugins" / FIXTURE, root / "plugins" / plugin_id, ignore=IGNORE)
        path = root / "plugins" / plugin_id / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["id"] = plugin_id
        manifest["source"]["path"] = f"plugins/{plugin_id}"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        cli.build_plugins(root, write_manifests=True)

    def test_the_body_has_the_documented_shape(self):
        body = cli.build_signed_feed(self.make_root(), "beta")
        self.assertEqual(set(body), {"schema_version", "name", "channel", "providers", "plugins"})
        self.assertEqual(body["schema_version"], 2)
        self.assertEqual(body["name"], cli.CATALOG_NAME)
        self.assertEqual(body["channel"], "beta")
        self.assertEqual([item["manifest"]["id"] for item in body["plugins"]], [FIXTURE])
        for field in ("signed_version", "not_before", "expires", "source_commit"):
            self.assertNotIn(field, body)

    def test_the_providers_are_the_ones_in_catalog_json(self):
        root = self.make_root()
        self.assertEqual(cli.build_signed_feed(root, "main")["providers"], cli.build_catalog(root)["providers"])
        full = cli.build_signed_feed(ROOT, "main")
        self.assertEqual(full["providers"], json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))["providers"])

    def test_plugins_are_sorted_by_id(self):
        root = self.make_root()
        for plugin_id in ("zeta_translator", "alpha_translator"):
            self.add_plugin(root, plugin_id)
        ids = [item["manifest"]["id"] for item in cli.build_signed_feed(root, "main")["plugins"]]
        self.assertEqual(ids, ["alpha_translator", FIXTURE, "zeta_translator"])

    def test_the_channel_must_be_main_or_beta(self):
        with self.assertRaisesRegex(cli.CatalogError, "channel must be one of main, beta"):
            cli.build_signed_feed(self.make_root(), "stable")

    def test_the_body_is_refused_when_a_plugin_is_invalid(self):
        root = self.make_root()
        (root / "plugins" / FIXTURE / "translator.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(cli.CatalogError, "sha mismatch"):
            cli.build_signed_feed(root, "main")

    def test_two_runs_give_the_same_bytes(self):
        root = self.make_root()
        first = cli.dump_json(cli.build_signed_feed(root, "main"))
        second = cli.dump_json(cli.build_signed_feed(root, "main"))
        self.assertEqual(first, second)

    def test_the_bytes_do_not_depend_on_where_or_when_the_tree_was_built(self):
        first_root = self.make_root()
        for plugin_id in ("zeta_translator", "alpha_translator"):
            self.add_plugin(first_root, plugin_id)
        second_root = self.make_root()
        # Create the plugins in the opposite order and age every file.
        for plugin_id in ("alpha_translator", "zeta_translator"):
            self.add_plugin(second_root, plugin_id)
        old = time.time() - 86400 * 400
        for path in second_root.rglob("*"):
            os.utime(path, (old, old))
        self.assertEqual(
            cli.dump_json(cli.build_signed_feed(first_root, "beta")),
            cli.dump_json(cli.build_signed_feed(second_root, "beta")),
        )

    def run_command(self, root, *args, seed="0"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        return subprocess.run(
            [sys.executable, "-B", "-m", "sdk", "build-signed-feed", str(root), *args],
            cwd=ROOT, capture_output=True, env=env,
        )

    def test_the_command_prints_the_same_bytes_under_any_hash_seed(self):
        root = self.make_root()
        outputs = {self.run_command(root, "--channel", "main", seed=seed).stdout for seed in ("0", "1", "12345")}
        self.assertEqual(len(outputs), 1)
        output = outputs.pop()
        self.assertEqual(output.decode("utf-8"), cli.dump_json(cli.build_signed_feed(root, "main")))
        self.assertTrue(output.endswith(b"\n"))

    def test_the_command_can_write_a_file(self):
        root = self.make_root()
        result = self.run_command(root, "--channel", "beta", "--output", "signed/beta/catalog.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        written = (root / "signed/beta/catalog.json").read_text(encoding="utf-8")
        self.assertEqual(written, cli.dump_json(cli.build_signed_feed(root, "beta")))

    def test_the_command_refuses_an_unknown_channel(self):
        result = self.run_command(self.make_root(), "--channel", "stable")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"invalid choice", result.stderr)

    def test_the_command_reports_an_invalid_plugin(self):
        root = self.make_root()
        (root / "plugins" / FIXTURE / "translator.py").write_text("x = 1\n", encoding="utf-8")
        result = self.run_command(root, "--channel", "main")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"sha mismatch", result.stderr)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
