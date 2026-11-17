"""Signing a channel's feed and checking it with the host's verifier, using keys made inside the test."""

import base64
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from sdk import cli

HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
if HAVE_CRYPTOGRAPHY:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from sdk import host_signing, signing

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "fixture_translator"
IGNORE = shutil.ignore_patterns("__pycache__")
NOW = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
ENV_NAME = "TEST_CATALOG_SIGNING_KEY"


def raw_public(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def pem(private_key):
    return private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def key_file_for(root_key, ci_keys, revoked=(), version=1):
    root_id = signing.key_id(raw_public(root_key))
    keys = [
        {
            "key_id": signing.key_id(raw_public(key)),
            "algorithm": "ed25519",
            "public_key": base64.b64encode(raw_public(key)).decode("ascii"),
            "added": "2026-11-17",
        }
        for key in ci_keys
    ]
    revocations = [{"key_id": signing.key_id(raw_public(key)), "revoked_at": "2026-11-18T00:00:00Z"} for key in revoked]
    statement = host_signing.key_file_statement(root_id, version, keys, revocations)
    return {
        "key_file_version": version,
        "root_key_id": root_id,
        "keys": keys,
        "revocations": revocations,
        "signature": base64.b64encode(root_key.sign(statement)).decode("ascii"),
    }


@unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
class SigningTestCase(unittest.TestCase):
    def setUp(self):
        self.root_key = Ed25519PrivateKey.generate()
        self.ci_key = Ed25519PrivateKey.generate()
        self.roots = {signing.key_id(raw_public(self.root_key)): raw_public(self.root_key)}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        shutil.copytree(ROOT / "providers/smoke", self.root / "providers/smoke", ignore=IGNORE)
        shutil.copytree(ROOT / "tests/fixtures/plugins" / FIXTURE, self.root / "plugins" / FIXTURE, ignore=IGNORE)
        self.write_key_file(key_file_for(self.root_key, [self.ci_key]))

    def write_key_file(self, document):
        path = self.root / signing.KEY_FILE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    def sign(self, channel="beta", now=NOW, **kwargs):
        kwargs.setdefault("source_commit", COMMIT)
        return signing.sign(self.root, channel, self.ci_key, now=now, roots=self.roots, **kwargs)

    def verify(self, channel="beta", now=NOW, **kwargs):
        return signing.verify_files(self.root, channel, now=now, roots=self.roots, **kwargs)

    def body(self, channel="beta"):
        return json.loads(signing.signed_paths(self.root, channel)[0].read_text(encoding="utf-8"))


class SignAndVerifyTests(SigningTestCase):
    def test_a_signed_pair_passes_the_host_verifier(self):
        result = self.sign()
        self.assertTrue(result.ok)
        self.assertEqual(result.signer_key_id, signing.key_id(raw_public(self.ci_key)))
        self.assertTrue(self.verify().ok)
        body_path, envelope_path = signing.signed_paths(self.root, "beta")
        self.assertTrue(body_path.is_file() and envelope_path.is_file())
        self.assertEqual(body_path.parent, self.root / "signed" / "beta")

    def test_the_body_is_the_unsigned_feed_plus_the_four_stamped_fields(self):
        self.sign()
        body = self.body()
        unsigned = cli.build_signed_feed(self.root, "beta")
        self.assertEqual({key: body[key] for key in unsigned}, unsigned)
        self.assertEqual(set(body) - set(unsigned), {"signed_version", "not_before", "expires", "source_commit"})
        self.assertEqual(body["channel"], "beta")
        self.assertEqual(body["signed_version"], int(NOW.timestamp()))
        self.assertEqual(body["not_before"], "2026-11-20T09:30:00Z")
        self.assertEqual(body["expires"], "2027-02-18T09:30:00Z")
        self.assertEqual(body["source_commit"], COMMIT)
        self.assertEqual([item["manifest"]["id"] for item in body["plugins"]], [FIXTURE])

    def test_validity_is_ninety_days_by_default_and_at_most_366(self):
        self.sign(validity_days=366)
        body = self.body()
        self.assertEqual(
            datetime.strptime(body["expires"], signing.TIMESTAMP_FORMAT) - datetime.strptime(body["not_before"], signing.TIMESTAMP_FORMAT),
            timedelta(days=366),
        )
        self.assertTrue(self.verify().ok)
        for days in (0, 367, -1, True, 1.5):
            with self.subTest(days=days), self.assertRaisesRegex(cli.CatalogError, "validity must be"):
                self.sign(now=NOW + timedelta(days=1), validity_days=days)

    def test_a_flipped_byte_is_refused(self):
        self.sign()
        body_path, _ = signing.signed_paths(self.root, "beta")
        data = bytearray(body_path.read_bytes())
        data[data.index(b"fixture_translator")] ^= 0x01
        body_path.write_bytes(bytes(data))
        self.assertEqual(self.verify().status.value, "bad_signature")

    def test_a_flipped_signature_byte_is_refused(self):
        self.sign()
        _, envelope_path = signing.signed_paths(self.root, "beta")
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        raw = bytearray(base64.b64decode(envelope["signatures"][0]["signature"]))
        raw[0] ^= 0x01
        envelope["signatures"][0]["signature"] = base64.b64encode(bytes(raw)).decode("ascii")
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        self.assertEqual(self.verify().status.value, "bad_signature")

    def test_a_file_checked_for_another_channel_is_refused(self):
        self.sign("beta")
        main_body, main_envelope = signing.signed_paths(self.root, "main")
        beta_body, beta_envelope = signing.signed_paths(self.root, "beta")
        main_body.parent.mkdir(parents=True)
        shutil.copy(beta_body, main_body)
        shutil.copy(beta_envelope, main_envelope)
        self.assertEqual(self.verify("main").status.value, "channel_mismatch")

    def test_a_key_the_root_revoked_is_refused(self):
        self.sign()
        self.write_key_file(key_file_for(self.root_key, [self.ci_key], revoked=[self.ci_key], version=2))
        self.assertEqual(self.verify().status.value, "revoked_key")
        with self.assertRaisesRegex(cli.CatalogError, "is revoked"):
            self.sign(now=NOW + timedelta(days=1))

    def test_a_key_the_file_does_not_list_is_refused_and_nothing_is_written(self):
        stranger = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(cli.CatalogError, "is not listed"):
            signing.sign(self.root, "beta", stranger, now=NOW, source_commit=COMMIT, roots=self.roots)
        self.assertFalse((self.root / "signed").exists())

    def test_a_key_file_from_an_untrusted_root_is_refused(self):
        self.write_key_file(key_file_for(Ed25519PrivateKey.generate(), [self.ci_key]))
        with self.assertRaisesRegex(cli.CatalogError, "not trusted"):
            self.sign()
        document = key_file_for(self.root_key, [self.ci_key])
        document["keys"][0]["added"] = "2030-01-01"
        self.write_key_file(document)
        with self.assertRaisesRegex(cli.CatalogError, "not signed by its root"):
            self.sign()

    def test_an_expired_window_is_refused_but_still_chains_when_expiry_is_ignored(self):
        self.sign()
        later = NOW + timedelta(days=92)
        self.assertEqual(self.verify(now=later).status.value, "expired")
        self.assertTrue(self.verify(now=later, baked=True).ok)

    def test_a_signing_clock_behind_the_last_signing_cannot_roll_the_channel_back(self):
        self.sign()
        before = signing.signed_paths(self.root, "beta")[0].read_bytes()
        with self.assertRaisesRegex(cli.CatalogError, "would not be higher"):
            self.sign(now=NOW - timedelta(hours=1), source_commit=OTHER_COMMIT)
        with self.assertRaisesRegex(cli.CatalogError, "would not be higher"):
            self.sign(now=NOW, source_commit=OTHER_COMMIT)
        self.assertEqual(signing.signed_paths(self.root, "beta")[0].read_bytes(), before)

    def test_signed_version_rises_with_every_signing(self):
        versions = []
        for step in range(3):
            self.sign(now=NOW + timedelta(seconds=step * 5))
            versions.append(self.body()["signed_version"])
        self.assertEqual(versions, sorted(set(versions)))


class SourceCommitTests(SigningTestCase):
    def test_the_source_commit_is_the_one_the_run_built_from(self):
        self.sign(source_commit=OTHER_COMMIT)
        self.assertEqual(self.body()["source_commit"], OTHER_COMMIT)

    def test_a_signing_needs_a_full_commit(self):
        for value in (None, "abc123", "A" * 40, "a" * 39, 7):
            with self.subTest(value=value), self.assertRaises(cli.CatalogError):
                self.sign(source_commit=value)
        self.assertFalse((self.root / "signed").exists())

    def test_a_renewal_keeps_the_source_commit_and_the_body_and_raises_the_version(self):
        self.sign(source_commit=COMMIT)
        first = self.body()
        later = NOW + timedelta(days=60)
        shutil.rmtree(self.root / "plugins")  # the tree moved on; a renewal must not read it
        self.sign(now=later, renew=True, source_commit=None)
        renewed = self.body()
        self.assertEqual(renewed["source_commit"], COMMIT)
        for field in ("providers", "plugins", "name", "channel", "schema_version"):
            self.assertEqual(renewed[field], first[field])
        self.assertGreater(renewed["signed_version"], first["signed_version"])
        self.assertEqual(renewed["not_before"], "2027-01-19T09:30:00Z")
        self.assertTrue(self.verify(now=later).ok)

    def test_a_renewal_refuses_another_commit_and_a_missing_body(self):
        with self.assertRaisesRegex(cli.CatalogError, "nothing to renew"):
            self.sign(renew=True, source_commit=None)
        self.sign()
        with self.assertRaisesRegex(cli.CatalogError, "keeps the source_commit"):
            self.sign(now=NOW + timedelta(days=1), renew=True, source_commit=OTHER_COMMIT)


class KeyHandlingTests(SigningTestCase):
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
            leftover = os.environ.get(ENV_NAME)
        return code, out.getvalue(), err.getvalue(), leftover

    def sign_args(self, *extra):
        return ("sign", str(self.root), "--channel", "beta", "--key-env", ENV_NAME, "--source-commit", COMMIT, *extra)

    def test_the_key_is_read_from_the_environment_and_never_printed(self):
        text = pem(self.ci_key).decode("ascii")
        # The cli verifies against the real root, so rehearse with the test roots.
        with mock.patch.object(signing, "ROOTS", self.roots):
            code, out, err, leftover = self.run_cli(*self.sign_args(), env={ENV_NAME: text})
        self.assertEqual(code, 0, err)
        body = "".join(text.strip().splitlines()[1:-1])
        for stream in (out, err):
            self.assertNotIn(body, stream)
            self.assertNotIn("PRIVATE KEY", stream)
        self.assertIn("signed beta: signed_version", out)
        self.assertIsNone(leftover, "the variable is removed from the process once the key is read")
        # The key is not on disk anywhere under the catalog root either.
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(body.encode("ascii"), path.read_bytes())

    def test_a_bad_key_is_refused_without_echoing_it(self):
        junk = "-----BEGIN PRIVATE KEY-----\nTOPSECRETNOTAKEY\n-----END PRIVATE KEY-----\n"
        code, out, err, _ = self.run_cli(*self.sign_args(), env={ENV_NAME: junk})
        self.assertEqual(code, 1)
        self.assertNotIn("TOPSECRETNOTAKEY", out + err)
        self.assertIn("not a readable", err)

    def test_a_missing_key_is_refused(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(ENV_NAME, None)
            code, _, err, _ = self.run_cli(*self.sign_args())
        self.assertEqual(code, 1)
        self.assertIn(ENV_NAME, err)

    def test_an_encrypted_or_foreign_key_is_refused(self):
        encrypted = self.ci_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"x"),
        ).decode("ascii")
        code, _, err, _ = self.run_cli(*self.sign_args(), env={ENV_NAME: encrypted})
        self.assertEqual(code, 1)
        self.assertIn("not a readable", err)

    def test_verify_reports_a_good_and_a_bad_pair(self):
        self.sign()
        with mock.patch.object(signing, "ROOTS", self.roots):
            code, out, err, _ = self.run_cli("verify", str(self.root), "--channel", "beta", "--now", "2026-11-21T00:00:00Z")
            self.assertEqual(code, 0, err)
            self.assertIn("beta ok", out)
            code, _, err, _ = self.run_cli("verify", str(self.root), "--channel", "beta", "--now", "2027-06-01T00:00:00Z")
            self.assertEqual(code, 1)
            self.assertIn("expired", err)
            code, _, _, _ = self.run_cli(
                "verify", str(self.root), "--channel", "beta", "--now", "2027-06-01T00:00:00Z", "--ignore-expiry"
            )
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
