"""The publish workflow keeps the key in one step, runs in the protected environment and cannot loop."""

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
if HAVE_CRYPTOGRAPHY:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from sdk import host_signing, signing

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/publish-signed.yml").read_text(encoding="utf-8")
MIRROR = (ROOT / ".github/workflows/pages-mirror.yml").read_text(encoding="utf-8")


def steps():
    """The workflow's steps as (name, text), split on the list markers of the steps block."""
    block = WORKFLOW.split("    steps:\n", 1)[1]
    parts = re.split(r"(?m)^      - ", block)[1:]
    return [(re.search(r"name: (.*)", part).group(1) if "name:" in part else "uses", part) for part in parts]


class PublishWorkflowTests(unittest.TestCase):
    def test_it_runs_on_the_protected_branches_only(self):
        self.assertIn("branches: [main, beta]", WORKFLOW)
        self.assertIn("github.ref == 'refs/heads/main' || github.ref == 'refs/heads/beta'", WORKFLOW)
        self.assertNotIn("pull_request", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)

    def test_the_job_waits_in_the_signing_environment(self):
        self.assertRegex(WORKFLOW, r"(?m)^    environment: catalog-signing$")

    def test_a_push_that_touches_only_signed_files_starts_nothing(self):
        match = re.search(r"paths-ignore:\n((?:\s+- .*\n)+)", WORKFLOW)
        self.assertIsNotNone(match)
        self.assertEqual(re.findall(r'- "(.*)"', match.group(1)), ["signed/**"])

    def test_the_commit_message_and_scope_are_the_agreed_ones(self):
        self.assertIn('git commit --quiet -m "chore(catalog): sign ${CHANNEL}"', WORKFLOW)
        self.assertIn('git add -- "signed/${CHANNEL}"', WORKFLOW)
        self.assertIn('git push origin "HEAD:refs/heads/${CHANNEL}"', WORKFLOW)
        self.assertNotIn("--force", WORKFLOW)

    def test_the_secret_reaches_exactly_one_step_and_only_as_an_environment_variable(self):
        self.assertEqual(WORKFLOW.count("secrets.CATALOG_SIGNING_KEY"), 1)
        holders = [name for name, text in steps() if "secrets.CATALOG_SIGNING_KEY" in text]
        self.assertEqual(holders, ["Sign"])
        sign = dict(steps())["Sign"]
        self.assertIn("CATALOG_SIGNING_KEY: ${{ secrets.CATALOG_SIGNING_KEY }}", sign)
        self.assertIn("--key-env CATALOG_SIGNING_KEY", sign)
        job_env = WORKFLOW.split("    env:\n", 1)[1].split("    steps:", 1)[0]
        self.assertNotIn("CATALOG_SIGNING_KEY", job_env)

    def test_nothing_echoes_traces_or_writes_the_key(self):
        for forbidden in ("set -x", "set -o xtrace", "xtrace", "add-mask", "$CATALOG_SIGNING_KEY", "${CATALOG_SIGNING_KEY}",
                          "ACTIONS_STEP_DEBUG", "> key", ".pem", "/dev/shm", "mktemp"):
            self.assertNotIn(forbidden, WORKFLOW, forbidden)
        self.assertNotIn("printenv", WORKFLOW)
        self.assertNotRegex(WORKFLOW, r"\benv\s*(\||$)")

    def test_the_run_starts_the_pages_mirror_because_a_token_push_cannot(self):
        self.assertIn('gh workflow run pages-mirror.yml --ref "$CHANNEL"', WORKFLOW)
        self.assertIn("workflow_dispatch:", MIRROR)
        self.assertIn("actions: write", WORKFLOW)
        self.assertIn("GH_TOKEN: ${{ github.token }}", WORKFLOW)

    def test_only_code_from_the_approved_commit_runs_next_to_the_key(self):
        # The checkout is the commit the run was created for: no ref override, and the branch is
        # never reset or checked out to a later tip before the signing step.
        checkout = steps()[0][1]
        self.assertNotIn("ref:", checkout)
        for forbidden in ("git checkout", "git merge", "git pull", "git switch", "git rebase", "-X theirs", "-X ours"):
            self.assertNotIn(forbidden, WORKFLOW, forbidden)
        # Moving to the tip happens once, in the push step, after the same stand-down rule, and the
        # signed files go over it whole.
        for name, text in steps():
            if name != "Commit the signed files to the branch":
                self.assertNotIn("git reset", text, name)
        push = dict(steps())["Commit the signed files to the branch"]
        self.assertLess(push.index(":(exclude)signed"), push.index("git reset --quiet --hard"))
        self.assertLess(push.index("git reset --quiet --hard"), push.index('cp "$out/catalog.json"'))
        # The pair about to be pushed is verified first, and nothing is pushed if that fails.
        self.assertLess(push.index("sdk verify --channel"), push.index("git push"))
        # A moved branch stands down in every mode, renewals included.
        guard = dict(steps())["Stand down if the branch has moved on"]
        standing_down = next(line for line in guard.splitlines() if 'if [ "$tip"' in line)
        self.assertNotIn("RENEW", standing_down)
        self.assertIn("proceed=false", guard)
        self.assertIn(":(exclude)signed", guard)
        # Everything after the guard runs only when it let the run proceed, and the install and the
        # tool come from the checkout.
        for name, text in steps()[2:]:
            self.assertIn("steps.head.outputs.proceed == 'true'", text, name)
        self.assertIn("-r sdk/requirements-signing.txt", WORKFLOW)

    def test_the_run_signs_one_channel_at_a_time_and_never_cancels_a_waiting_run(self):
        self.assertIn("group: publish-signed-${{ github.ref_name }}", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)

    def test_the_dependencies_are_installed_from_the_hash_pinned_file(self):
        self.assertIn("--require-hashes --only-binary=:all: -r sdk/requirements-signing.txt", WORKFLOW)
        text = (ROOT / "sdk/requirements-signing.txt").read_text(encoding="utf-8")
        for pin in ("cryptography==", "cffi==", "pycparser=="):
            self.assertIn(pin, text)
        self.assertEqual(len(re.findall(r"--hash=sha256:[0-9a-f]{64}", text)), len(re.findall(r"--hash=", text)))


class PushGuardTests(unittest.TestCase):
    """Runs the workflow's commit step against real repositories standing in for origin."""

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-c", "init.defaultBranch=beta", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit_file(self, repo, path, text, message):
        target = Path(repo) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git(repo, "add", path)
        self.git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.origin = base / "origin.git"
        self.git(base, "init", "-q", "--bare", str(self.origin))
        self.work = base / "work"
        self.git(base, "clone", "-q", str(self.origin), str(self.work))
        self.commit_file(self.work, "README.md", "one\n", "first")
        self.git(self.work, "push", "-q", "origin", "HEAD:refs/heads/beta")
        self.source = self.git(self.work, "rev-parse", "HEAD")
        self.other = base / "other"
        self.git(base, "clone", "-q", str(self.origin), str(self.other))
        (self.work / "signed" / "beta").mkdir(parents=True)
        (self.work / "signed" / "beta" / "catalog.json").write_text("signed\n", encoding="utf-8")
        (self.work / "signed" / "beta" / "catalog.json.sig").write_text("signed sig\n", encoding="utf-8")
        self.output = base / "github_output"
        self.output.write_text("", encoding="utf-8")
        (base / "runner").mkdir()
        self.stub_verifier = base / "verify-ok.sh"
        self.stub_verifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.stub_verifier.chmod(0o755)

    def run_step(self, name="Commit the signed files to the branch", cwd=None, renew="false", signing_python=None):
        script = dict(steps())[name].split("run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        env = {
            **os.environ,
            "CHANNEL": "beta",
            "RENEW": renew,
            "SOURCE_COMMIT": self.source,
            "GITHUB_OUTPUT": str(self.output),
            "RUNNER_TEMP": str(self.output.parent / "runner"),
            "SIGNING_PYTHON": str(signing_python or self.stub_verifier),
        }
        return subprocess.run(["bash", "-e", "-c", script], cwd=cwd or self.work, env=env, capture_output=True, text=True)

    def rerun_clone(self, content):
        """A fresh checkout of the signed commit, as a re-run of the same job starts, with a new signature."""
        clone = self.output.parent / f"rerun-{content}"
        self.git(self.output.parent, "clone", "-q", str(self.origin), str(clone))
        self.git(clone, "checkout", "-q", "--detach", self.source)
        (clone / "signed" / "beta").mkdir(parents=True)
        (clone / "signed" / "beta" / "catalog.json").write_text(content + "\n", encoding="utf-8")
        (clone / "signed" / "beta" / "catalog.json.sig").write_text(content + " sig\n", encoding="utf-8")
        return clone

    def tip_file(self, path):
        return self.git(self.origin, "show", f"refs/heads/beta:{path}")

    def tip(self):
        return self.git(self.origin, "rev-parse", "refs/heads/beta")

    def test_a_non_signed_push_during_the_run_stands_down_without_pushing(self):
        self.commit_file(self.other, "providers/new.txt", "newer\n", "feat: newer push")
        self.git(self.other, "push", "-q", "origin", "HEAD:refs/heads/beta")
        newer = self.tip()
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tip(), newer)
        self.assertIn("pushed=false", self.output.read_text(encoding="utf-8"))

    def test_a_signed_only_commit_on_the_tip_is_rebased_across(self):
        self.commit_file(self.other, "signed/main/catalog.json", "other channel\n", "chore(catalog): sign main")
        self.git(self.other, "push", "-q", "origin", "HEAD:refs/heads/beta")
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pushed=true", self.output.read_text(encoding="utf-8"))
        log = self.git(self.origin, "log", "--format=%s", "refs/heads/beta")
        self.assertEqual(log.splitlines()[:2], ["chore(catalog): sign beta", "chore(catalog): sign main"])

    def test_an_unmoved_branch_gets_the_sign_commit(self):
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.origin, "log", "--format=%s", "-1", "refs/heads/beta"), "chore(catalog): sign beta")

    def test_a_rerun_after_its_own_signing_commit_replaces_the_signature_instead_of_conflicting(self):
        self.assertEqual(self.run_step().returncode, 0)
        self.assertEqual(self.tip_file("signed/beta/catalog.json"), "signed")
        again = self.rerun_clone("signed again")
        self.output.write_text("", encoding="utf-8")
        result = self.run_step(cwd=again)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pushed=true", self.output.read_text(encoding="utf-8"))
        self.assertEqual(self.tip_file("signed/beta/catalog.json"), "signed again")
        self.assertEqual(self.git(self.origin, "show", "refs/heads/beta:README.md"), "one")

    def write_signed_body(self, source_commit):
        (self.work / "signed" / "beta" / "catalog.json").write_text(
            '{"channel": "beta", "source_commit": "%s"}\n' % source_commit, encoding="utf-8"
        )
        (self.work / "signed" / "beta" / "catalog.json.sig").write_text("sig\n", encoding="utf-8")

    def test_a_rerun_skips_signing_when_the_tip_already_signs_this_commit(self):
        self.write_signed_body(self.source)
        self.assertEqual(self.run_step().returncode, 0)
        clone = self.output.parent / "again"
        self.git(self.output.parent, "clone", "-q", str(self.origin), str(clone))
        self.git(clone, "checkout", "-q", "--detach", self.source)
        self.output.write_text("", encoding="utf-8")
        result = self.run_step("Stand down if the branch has moved on", cwd=clone)
        self.assertEqual(result.returncode, 0, result.stderr)
        written = self.output.read_text(encoding="utf-8")
        self.assertIn("proceed=true", written)
        self.assertIn("sign=false", written)

    def test_signing_goes_ahead_when_the_tip_signs_another_commit(self):
        self.write_signed_body("c" * 40)
        self.assertEqual(self.run_step().returncode, 0)
        clone = self.output.parent / "again"
        self.git(self.output.parent, "clone", "-q", str(self.origin), str(clone))
        self.git(clone, "checkout", "-q", "--detach", self.source)
        self.output.write_text("", encoding="utf-8")
        result = self.run_step("Stand down if the branch has moved on", cwd=clone)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sign=true", self.output.read_text(encoding="utf-8"))

    def test_a_renewal_never_treats_a_signing_of_the_same_commit_as_a_repeat_and_stands_down(self):
        self.write_signed_body(self.source)
        self.assertEqual(self.run_step().returncode, 0)
        clone = self.output.parent / "again"
        self.git(self.output.parent, "clone", "-q", str(self.origin), str(clone))
        self.git(clone, "checkout", "-q", "--detach", self.source)
        self.output.write_text("", encoding="utf-8")
        result = self.run_step("Stand down if the branch has moved on", cwd=clone, renew="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        written = self.output.read_text(encoding="utf-8")
        self.assertIn("proceed=false", written)
        self.assertNotIn("sign=false", written)

    def test_every_signing_step_is_skipped_for_a_repeat_and_the_mirror_build_still_starts(self):
        for name, text in steps()[2:]:
            if name == "Start the Pages mirror build":
                self.assertNotIn("outputs.sign", text)
            else:
                self.assertIn("steps.head.outputs.sign == 'true'", text, name)

    def real_verifier(self, roots):
        """Stands in for the signing venv's python: runs the real `sdk verify` with the test's roots."""
        base = self.output.parent
        script = base / "verify_with_test_roots.py"
        script.write_text(
            "import base64, json, os, sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from sdk import cli, signing\n"
            "signing.ROOTS.clear()\n"
            "signing.ROOTS.update({k: base64.b64decode(v) for k, v in json.loads(os.environ['TEST_ROOTS']).items()})\n"
            "raise SystemExit(cli.main(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        wrapper = base / "verify-real.sh"
        wrapper.write_text(f'#!/bin/sh\nshift 3\nexec {sys.executable} -B {script} "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        os.environ["TEST_ROOTS"] = json.dumps({k: base64.b64encode(v).decode() for k, v in roots.items()})
        self.addCleanup(os.environ.pop, "TEST_ROOTS", None)
        return wrapper

    def prepare_real_repo(self):
        """A branch holding an older signed pair made with throwaway keys; returns what the runs need."""
        def raw(key):
            return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

        root_key, ci_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        roots = {signing.key_id(raw(root_key)): raw(root_key)}
        keys = [{
            "key_id": signing.key_id(raw(ci_key)), "algorithm": "ed25519",
            "public_key": base64.b64encode(raw(ci_key)).decode(), "added": "2026-11-17",
        }]
        statement = host_signing.key_file_statement(signing.key_id(raw(root_key)), 1, keys, [])
        key_file = {
            "key_file_version": 1, "root_key_id": signing.key_id(raw(root_key)), "keys": keys,
            "revocations": [], "signature": base64.b64encode(root_key.sign(statement)).decode(),
        }
        verifier = self.real_verifier(roots)
        now = datetime.now(timezone.utc).replace(microsecond=0)

        # The branch holds an older signed pair (Pold), as a renewal would find it.
        shutil.rmtree(self.work / "signed")
        shutil.copytree(ROOT / "providers/smoke", self.work / "providers/smoke", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "tests/fixtures/plugins/fixture_translator",
                        self.work / "plugins/fixture_translator", ignore=shutil.ignore_patterns("__pycache__"))
        (self.work / "keys").mkdir()
        (self.work / "keys/catalog-signing-keys.json").write_text(json.dumps(key_file), encoding="utf-8")
        signing.sign(self.work, "beta", ci_key, now=now - timedelta(days=5), source_commit="d" * 40, roots=roots)
        self.git(self.work, "add", ".")
        self.git(self.work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "content and old pair")
        self.git(self.work, "push", "-q", "origin", "HEAD:refs/heads/beta")
        self.source = self.git(self.work, "rev-parse", "HEAD")

        return roots, ci_key, verifier, now

    def checkout(self, name):
        clone = self.output.parent / name
        self.git(self.output.parent, "clone", "-q", str(self.origin), str(clone))
        self.git(clone, "checkout", "-q", "--detach", self.source)
        return clone

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
    def test_a_renewal_behind_a_normal_signing_stands_down_and_leaves_the_newer_pair(self):
        roots, ci_key, verifier, now = self.prepare_real_repo()
        # Both runs were created for this commit. The normal signing pushes first.
        normal, renewal = self.checkout("normal"), self.checkout("renewal")
        signing.sign(normal, "beta", ci_key, now=now - timedelta(days=2), source_commit=self.source, roots=roots)
        self.assertEqual(self.run_step(cwd=normal, signing_python=verifier).returncode, 0)
        pushed = self.tip()
        # The renewal restamps the old body from its own checkout. Landing it would roll the channel back.
        signing.sign(renewal, "beta", ci_key, now=now - timedelta(hours=1), renew=True, roots=roots)
        self.output.write_text("", encoding="utf-8")
        result = self.run_step(cwd=renewal, renew="true", signing_python=verifier)
        self.assertEqual(result.returncode, 0, result.stderr)
        written = self.output.read_text(encoding="utf-8")
        self.assertIn("pushed=false", written)
        self.assertNotIn("pushed=true", written)
        self.assertEqual(self.tip(), pushed)
        landed = self.checkout("landed")
        self.git(landed, "checkout", "-q", "--detach", "origin/beta")
        for name in ("catalog.json", "catalog.json.sig"):
            self.assertEqual((landed / "signed/beta" / name).read_bytes(), (normal / "signed/beta" / name).read_bytes())
        self.assertEqual(json.loads((landed / "signed/beta/catalog.json").read_text())["source_commit"], self.source)
        self.assertTrue(signing.verify_files(landed, "beta", now=now, roots=roots).ok)
        # The mirror build is gated on the step not having stood down.
        self.assertIn("steps.push.outputs.pushed != 'false'", dict(steps())["Start the Pages mirror build"])

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
    def test_a_renewal_on_a_tip_it_renewed_lands_a_pair_that_verifies(self):
        roots, ci_key, verifier, now = self.prepare_real_repo()
        renewal = self.checkout("renewal")
        signing.sign(renewal, "beta", ci_key, now=now - timedelta(hours=1), renew=True, roots=roots)
        self.output.write_text("", encoding="utf-8")
        result = self.run_step(cwd=renewal, renew="true", signing_python=verifier)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pushed=true", self.output.read_text(encoding="utf-8"))
        landed = self.checkout("landed")
        self.git(landed, "checkout", "-q", "--detach", "origin/beta")
        for name in ("catalog.json", "catalog.json.sig"):
            self.assertEqual((landed / "signed/beta" / name).read_bytes(), (renewal / "signed/beta" / name).read_bytes())
        self.assertTrue(signing.verify_files(landed, "beta", now=now, roots=roots).ok)

    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
    def test_a_renewal_that_finds_a_newer_pair_at_the_start_never_signs(self):
        roots, ci_key, verifier, now = self.prepare_real_repo()
        normal, renewal = self.checkout("normal"), self.checkout("renewal")
        signing.sign(normal, "beta", ci_key, now=now - timedelta(days=2), source_commit=self.source, roots=roots)
        self.assertEqual(self.run_step(cwd=normal, signing_python=verifier).returncode, 0)
        guard = "Stand down if the branch has moved on"
        for renew, expected in (("true", "proceed=false"), ("false", "proceed=true")):
            self.output.write_text("", encoding="utf-8")
            result = self.run_step(guard, cwd=renewal, renew=renew)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, self.output.read_text(encoding="utf-8"), renew)

    def test_a_signing_that_is_not_newer_than_the_tip_is_never_pushed(self):
        self.commit_file(self.other, "signed/beta/catalog.json", '{"signed_version": 10}\n', "chore(catalog): sign beta")
        self.commit_file(self.other, "signed/beta/catalog.json.sig", "sig\n", "sig")
        self.git(self.other, "push", "-q", "origin", "HEAD:refs/heads/beta")
        before = self.tip()
        for version in (9, 10):
            (self.work / "signed/beta/catalog.json").write_text('{"signed_version": %d}\n' % version, encoding="utf-8")
            result = self.run_step()
            self.assertNotEqual(result.returncode, 0, version)
            self.assertIn("not greater", result.stderr + result.stdout)
            self.assertEqual(self.tip(), before)
            self.git(self.work, "reset", "-q", "--hard", self.source)
            (self.work / "signed/beta").mkdir(parents=True, exist_ok=True)
            (self.work / "signed/beta/catalog.json.sig").write_text("signed sig\n", encoding="utf-8")
        (self.work / "signed/beta/catalog.json").write_text('{"signed_version": 11}\n', encoding="utf-8")
        self.assertEqual(self.run_step().returncode, 0)
        self.assertNotEqual(self.tip(), before)

    def test_a_pair_that_does_not_verify_is_never_pushed(self):
        failing = self.output.parent / "verify-fails.sh"
        failing.write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8")
        failing.chmod(0o755)
        before = self.tip()
        result = self.run_step(signing_python=failing)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tip(), before)

    def test_the_mirror_build_is_not_started_after_a_stand_down(self):
        self.assertIn("steps.push.outputs.pushed != 'false'", dict(steps())["Start the Pages mirror build"])


if __name__ == "__main__":
    unittest.main()
