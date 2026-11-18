"""The Google Translate plugin as a catalog entry: a locked closure, baked wheels, and a feed that signs."""

import base64
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sdk import cli

HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
if HAVE_CRYPTOGRAPHY:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from sdk import host_signing, signing

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "google_translate"
WHEELS = PLUGIN_DIR / "wheels"
GOOGLE_ORIGINS = ["https://translate.google.com:443", "https://translate.googleapis.com:443"]


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_identity(filename):
    name, version, *tags = filename[:-4].split("-")
    return normalized(name), version, "-".join(tags[-3:])


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))

    def test_the_catalog_validator_accepts_the_entry(self):
        manifests = cli.build_plugins(ROOT)
        entry = next(item for item in manifests if item["id"] == "google_translate")
        self.assertEqual(entry["kind"], "translator")
        self.assertEqual(entry["schema_version"], 2)

    def test_it_is_pinned_to_this_repository_and_to_its_own_directory(self):
        self.assertEqual(self.manifest["source"]["type"], "github")
        self.assertEqual(self.manifest["source"]["repo"], "LavX/bazarr-provider-catalog")
        self.assertEqual(self.manifest["source"]["path"], "plugins/google_translate")

    def test_both_google_origins_are_declared_and_the_network_is_the_only_permission(self):
        self.assertEqual(self.manifest["endpoints"], GOOGLE_ORIGINS)
        self.assertEqual(self.manifest["permissions"], ["network"])

    def test_the_bundle_carries_the_code_and_the_wheels_and_no_development_files(self):
        self.assertEqual(
            sorted(self.manifest["files"]),
            ["google_translate.py"] + sorted(f"wheels/{path.name}" for path in WHEELS.iterdir()),
        )
        self.assertEqual(sorted(path.name for path in PLUGIN_DIR.iterdir() if path.is_file()), ["google_translate.py", "plugin.json"])

    def test_every_baked_wheel_is_pure_python_so_the_manifest_says_py3(self):
        self.assertEqual(self.manifest["python_tags"], ["py3"])
        self.assertTrue(list(WHEELS.glob("*.whl")))
        for path in WHEELS.iterdir():
            self.assertTrue(path.name.endswith("-py3-none-any.whl"), path.name)


class BakedWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        cls.requirements = {normalized(item["name"]): item for item in cls.manifest["dependencies"]["requirements"]}

    def test_each_locked_dependency_has_exactly_one_baked_wheel_that_its_lock_names(self):
        baked = {}
        for path in sorted(WHEELS.glob("*.whl")):
            name, version, _tags = wheel_identity(path.name)
            self.assertNotIn(name, baked, f"two wheels of {name}")
            baked[name] = (version, path)
        self.assertEqual(sorted(baked), sorted(self.requirements))
        for name, (version, path) in baked.items():
            requirement = self.requirements[name]
            self.assertEqual(version, requirement["version"], name)
            self.assertIn(f"sha256:{sha256_of(path)}", requirement["hashes"], name)

    def test_the_closure_is_complete_by_the_wheels_own_metadata(self):
        # Every unconditional Requires-Dist of a baked wheel is a locked dependency.
        locked = set(self.requirements)
        for path in sorted(WHEELS.glob("*.whl")):
            with zipfile.ZipFile(path) as wheel:
                metadata = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
                text = wheel.read(metadata).decode("utf-8")
            for line in text.splitlines():
                if not line.startswith("Requires-Dist:") or "extra ==" in line:
                    continue
                match = re.match(r"Requires-Dist:\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
                if match and ";" not in line:
                    self.assertIn(normalized(match.group(1)), locked, f"{path.name} needs {line}")

    def test_the_baked_wheels_install_offline_against_the_locked_hashes(self):
        # A hash-checked install with no index. A dependency missing from the lock or from wheels/ makes pip refuse.
        if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0:
            self.skipTest("pip is not available")
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "requirements.txt"
            lock.write_text(
                "".join(
                    "{}=={} {}\n".format(item["name"], item["version"], " ".join(f"--hash={digest}" for digest in item["hashes"]))
                    for item in self.manifest["dependencies"]["requirements"]
                ),
                encoding="utf-8",
            )
            target = Path(tmp) / "site"
            result = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                    "--quiet", "--no-index", "--find-links", str(WHEELS), "--require-hashes", "--only-binary=:all:",
                    "--target", str(target), "-r", str(lock),
                ],
                capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "HOME": tmp},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = {path.name.split("-")[0].lower().replace("_", "-") for path in target.glob("*.dist-info")}
            self.assertEqual(installed, set(self.requirements))
            # What the plugin imports comes from this installation, at the baked version, and not from
            # whatever else is installed where the tests run.
            probe = subprocess.run(
                [
                    sys.executable, "-I", "-c",
                    "import sys; sys.path.insert(0, sys.argv[1])\n"
                    "import deep_translator, bs4, requests, importlib.metadata as m\n"
                    "print(m.version('deep-translator'))\n"
                    "print(deep_translator.__file__, bs4.__file__, requests.__file__, sep='\\n')",
                    str(target),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            version, *paths = probe.stdout.splitlines()
            self.assertEqual(version, "1.11.4")
            for path in paths:
                self.assertTrue(Path(path).resolve().is_relative_to(target.resolve()), path)

    def test_a_wheel_that_does_not_match_its_hash_would_not_pass_the_manifest_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "plugins" / "google_translate"
            shutil.copytree(PLUGIN_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
            wheel = next(copy.glob("wheels/*.whl"))
            wheel.write_bytes(wheel.read_bytes() + b"\0")
            with self.assertRaises(cli.CatalogError) as raised:
                cli.validate_plugin(Path(tmp), copy, set())
            self.assertIn("sha mismatch", str(raised.exception))

    def test_an_undeclared_wheel_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "plugins" / "google_translate"
            shutil.copytree(PLUGIN_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
            (copy / "wheels" / "extra-1.0-py3-none-any.whl").write_bytes(b"x")
            with self.assertRaises(cli.CatalogError) as raised:
                cli.validate_plugin(Path(tmp), copy, set())
            self.assertIn("undeclared file", str(raised.exception))


class BakedWheelValidationTests(unittest.TestCase):
    """`sdk validate` refuses a bundle whose wheels the lock does not name, whatever the plugin."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.copy = self.root / "plugins" / "google_translate"
        shutil.copytree(PLUGIN_DIR, self.copy, ignore=shutil.ignore_patterns("__pycache__"))

    def refresh_and_validate(self):
        manifest = cli.refresh_plugin_manifest(self.copy)
        (self.copy / "plugin.json").write_text(cli.dump_json(manifest), encoding="utf-8")
        return cli.validate_plugin(self.root, self.copy, set())

    def refused(self, fragment):
        with self.assertRaises(cli.CatalogError) as raised:
            self.refresh_and_validate()
        self.assertIn(fragment, str(raised.exception))

    def test_the_committed_bundle_passes(self):
        self.refresh_and_validate()

    def test_a_wheel_of_a_package_the_lock_does_not_name_is_refused(self):
        (self.copy / "wheels" / "extra-1.0-py3-none-any.whl").write_bytes(b"x")
        self.refused("not a locked dependency")

    def test_a_wheel_of_another_version_is_refused(self):
        wheel = next(self.copy.glob("wheels/idna-*.whl"))
        wheel.rename(wheel.with_name("idna-9.9-py3-none-any.whl"))
        self.refused("not the locked version")

    def test_a_wheel_whose_hash_the_lock_does_not_list_is_refused(self):
        wheel = next(self.copy.glob("wheels/idna-*.whl"))
        wheel.write_bytes(wheel.read_bytes() + b"\0")
        self.refused("not among the hashes locked")

    def test_a_locked_dependency_without_a_wheel_is_refused(self):
        next(self.copy.glob("wheels/idna-*.whl")).unlink()
        self.refused("carries no wheel of it")

    def test_a_file_under_wheels_that_is_not_a_wheel_is_refused(self):
        (self.copy / "wheels" / "notes.txt").write_text("x", encoding="utf-8")
        self.refused("not a wheel")
        shutil.rmtree(self.copy / "wheels" / "notes.txt", ignore_errors=True)

    def test_a_plugin_with_no_wheels_is_not_held_to_this(self):
        shutil.rmtree(self.copy / "wheels")
        self.refresh_and_validate()


class FeedTests(unittest.TestCase):
    def test_the_signed_feed_carries_the_entry_with_its_files_and_the_legacy_catalog_does_not(self):
        body = cli.build_signed_feed(ROOT, "beta")
        entry = next(item["manifest"] for item in body["plugins"] if item["manifest"]["id"] == "google_translate")
        self.assertEqual(entry["files"], json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))["files"])
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        self.assertNotIn("plugins", catalog)
        self.assertNotIn("google_translate", json.dumps(catalog))

    def test_the_catalog_json_is_unchanged_by_the_plugin(self):
        self.assertEqual(
            cli.dump_json(cli.build_catalog(ROOT)).encode("utf-8"), (ROOT / "catalog.json").read_bytes()
        )

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
    def test_a_signing_run_signs_it_and_the_hosts_verifier_accepts_the_pair(self):
        def raw(key):
            return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

        root_key, ci_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        root_id = signing.key_id(raw(root_key))
        roots = {root_id: raw(root_key)}
        keys = [{
            "key_id": signing.key_id(raw(ci_key)), "algorithm": "ed25519",
            "public_key": base64.b64encode(raw(ci_key)).decode(), "added": "2026-11-17",
        }]
        statement = host_signing.key_file_statement(root_id, 1, keys, [])
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            ignore = shutil.ignore_patterns("__pycache__")
            shutil.copytree(ROOT / "providers" / "smoke", work / "providers" / "smoke", ignore=ignore)
            shutil.copytree(PLUGIN_DIR, work / "plugins" / "google_translate", ignore=ignore)
            shutil.copy(ROOT / "catalog.json", work / "catalog.json")
            (work / "keys").mkdir()
            (work / "keys" / "catalog-signing-keys.json").write_text(json.dumps({
                "key_file_version": 1, "root_key_id": root_id, "keys": keys, "revocations": [],
                "signature": base64.b64encode(root_key.sign(statement)).decode(),
            }), encoding="utf-8")
            now = datetime(2026, 12, 1, 6, 17, tzinfo=timezone.utc)
            result = signing.sign(work, "beta", ci_key, now=now, source_commit="e" * 40, roots=roots)
            self.assertTrue(result.ok)
            self.assertTrue(signing.verify_files(work, "beta", now=now, roots=roots).ok)
            body = json.loads((work / "signed" / "beta" / "catalog.json").read_text(encoding="utf-8"))
            manifest = next(item["manifest"] for item in body["plugins"] if item["manifest"]["id"] == "google_translate")
            self.assertEqual(manifest["endpoints"], GOOGLE_ORIGINS)
            self.assertEqual(len([name for name in manifest["files"] if name.startswith("wheels/")]), 9)


if __name__ == "__main__":
    unittest.main()
