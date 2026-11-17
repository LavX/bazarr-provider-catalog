"""What the repository itself carries for signing: the root, the key file, the signed pairs and the verifier copy."""

import base64
import hashlib
import importlib.util
import json
import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sdk import cli, released_hosts

HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
if HAVE_CRYPTOGRAPHY:
    from sdk import host_signing, signing

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ("main", "beta")
ROOT_KEY_ID = "d2edde7047b7b2fe"
CI_KEY_ID = "e5a4609103600fbd"
PRIVATE_KEY_MARKER = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")


@unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
class CommittedSigningTests(unittest.TestCase):
    def test_the_trusted_root_is_the_one_the_release_ships(self):
        self.assertEqual(list(signing.ROOTS), [ROOT_KEY_ID])
        public = signing.ROOTS[ROOT_KEY_ID]
        self.assertEqual(len(public), 32)
        self.assertEqual(hashlib.sha256(public).hexdigest()[:16], ROOT_KEY_ID)
        self.assertEqual(host_signing.key_id(public), ROOT_KEY_ID)

    def test_the_key_file_is_signed_by_the_root_and_lists_a_usable_ci_key(self):
        # parse_key_file refuses a file the trusted root did not sign, so any revocation it
        # returns is authenticated. Revocations are allowed; at least one listed key must survive.
        key_file = signing.parse_key_file(signing.read_key_file(ROOT))
        self.assertEqual(key_file.root_key_id, ROOT_KEY_ID)
        revoked = {item.key_id for item in key_file.revocations}
        listed = [key.key_id for key in key_file.keys]
        self.assertTrue(set(listed) - revoked, "every listed signing key is revoked")
        self.assertNotIn(ROOT_KEY_ID, listed)

    def test_the_key_file_matches_the_public_records_of_both_keys(self):
        document = json.loads((ROOT / signing.KEY_FILE_PATH).read_text(encoding="utf-8"))
        entries = {item["key_id"]: item for item in document["keys"]}
        self.assertIn(CI_KEY_ID, entries, "the key made on 2026-11-17 is listed")
        for key_id, entry in entries.items():
            self.assertEqual(signing.key_id(base64.b64decode(entry["public_key"])), key_id)
            self.assertEqual(entry["algorithm"], "ed25519")

    def test_the_key_file_holds_no_private_material(self):
        self.assertIsNone(PRIVATE_KEY_MARKER.search((ROOT / signing.KEY_FILE_PATH).read_bytes()))
        self.assertIsNone(PRIVATE_KEY_MARKER.search((ROOT / "sdk" / "signing.py").read_bytes()))

    def test_every_committed_signed_pair_verifies_against_the_committed_key_file(self):
        # An existing pair is checked separately from the mandatory verification of newly signed
        # output (sdk sign and the workflow's verify step). Signature and chain only: a pair may
        # outlive its window until the next signing run. After the root revokes the key that signed
        # a pair, the pair reads as revoked until a new signing replaces it, and that is allowed.
        found = 0
        for channel in CHANNELS:
            body_path, envelope_path = signing.signed_paths(ROOT, channel)
            if not body_path.exists() and not envelope_path.exists():
                continue
            found += 1
            with self.subTest(channel=channel):
                result = signing.verify_files(ROOT, channel, now=datetime.now(timezone.utc), baked=True)
                if result.status.value == "revoked_key":
                    key_file = signing.parse_key_file(signing.read_key_file(ROOT))
                    self.assertIn(result.signer_key_id, {item.key_id for item in key_file.revocations})
                else:
                    self.assertTrue(result.ok, f"{result.status.value}: {result.detail}")
                    self.assertIn(result.signer_key_id, {key.key_id for key in signing.parse_key_file(signing.read_key_file(ROOT)).keys})
                body = json.loads(body_path.read_text(encoding="utf-8"))
                self.assertEqual(body["channel"], channel)
                self.assertRegex(body["source_commit"], r"^[a-f0-9]{40}$")
        if not found:
            self.skipTest("no signed pair is committed yet")

    def test_the_verifier_copy_has_not_drifted(self):
        meta = json.loads((ROOT / "sdk" / "host_signing.json").read_text(encoding="utf-8"))
        vendored = (ROOT / "sdk" / "host_signing.py").read_bytes()
        self.assertEqual(released_hosts.git_blob_id(vendored), meta["vendored_blob"])
        lines = vendored.split(b"\n")
        original = b"\n".join(lines[:1] + lines[1 + meta["notice_lines"]:])
        self.assertEqual(released_hosts.git_blob_id(original), meta["source_blob"])
        self.assertRegex(meta["bazarr_commit"], r"^[a-f0-9]{40}$")

    @unittest.skipUnless(os.environ.get("BAZARR_PROVIDER_HUB_DIR"), "set BAZARR_PROVIDER_HUB_DIR to a bazarr/provider_hub checkout")
    def test_the_verifier_and_the_root_match_the_host_checkout(self):
        hub = Path(os.environ["BAZARR_PROVIDER_HUB_DIR"])
        meta = json.loads((ROOT / "sdk" / "host_signing.json").read_text(encoding="utf-8"))
        self.assertEqual(released_hosts.git_blob_id((hub / "signing.py").read_bytes()), meta["source_blob"])
        roots = (hub / "signing_roots.py").read_text(encoding="utf-8")
        for key_id, public in signing.ROOTS.items():
            self.assertIn(f'"{key_id}"', roots)
            self.assertIn(base64.b64encode(public).decode("ascii"), roots)


class LegacyFeedTests(unittest.TestCase):
    def test_signing_does_not_touch_the_legacy_catalog(self):
        self.assertEqual(
            (ROOT / "catalog.json").read_text(encoding="utf-8"),
            cli.dump_json(cli.build_catalog(ROOT)),
        )


if __name__ == "__main__":
    unittest.main()
