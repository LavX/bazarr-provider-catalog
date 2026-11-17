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

    def test_the_key_file_is_signed_by_the_root_and_lists_the_ci_key(self):
        key_file = signing.parse_key_file(signing.read_key_file(ROOT))
        self.assertEqual(key_file.root_key_id, ROOT_KEY_ID)
        self.assertIn(CI_KEY_ID, [key.key_id for key in key_file.keys])
        self.assertEqual(key_file.revocations, ())
        self.assertNotIn(ROOT_KEY_ID, [key.key_id for key in key_file.keys])

    def test_the_key_file_matches_the_public_records_of_both_keys(self):
        document = json.loads((ROOT / signing.KEY_FILE_PATH).read_text(encoding="utf-8"))
        entry = next(item for item in document["keys"] if item["key_id"] == CI_KEY_ID)
        self.assertEqual(signing.key_id(base64.b64decode(entry["public_key"])), CI_KEY_ID)
        self.assertEqual(entry["algorithm"], "ed25519")

    def test_the_key_file_holds_no_private_material(self):
        self.assertIsNone(PRIVATE_KEY_MARKER.search((ROOT / signing.KEY_FILE_PATH).read_bytes()))
        self.assertIsNone(PRIVATE_KEY_MARKER.search((ROOT / "sdk" / "signing.py").read_bytes()))

    def test_every_committed_signed_pair_verifies_against_the_committed_key_file(self):
        # Signature and chain only: a pair may outlive its window until the next signing run.
        found = 0
        for channel in CHANNELS:
            body_path, envelope_path = signing.signed_paths(ROOT, channel)
            if not body_path.exists() and not envelope_path.exists():
                continue
            found += 1
            with self.subTest(channel=channel):
                result = signing.verify_files(ROOT, channel, now=datetime.now(timezone.utc), baked=True)
                self.assertTrue(result.ok, f"{result.status.value}: {result.detail}")
                self.assertEqual(result.signer_key_id, CI_KEY_ID)
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
