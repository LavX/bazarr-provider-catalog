"""The publish workflow keeps the key in one step, runs in the protected environment and cannot loop."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

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
        for forbidden in ("git reset", "git checkout", "git merge", "git pull", "git switch"):
            self.assertNotIn(forbidden, WORKFLOW, forbidden)
        # A moved branch stands down in every mode, renewals included.
        guard = dict(steps())["Stand down if the branch has moved on"]
        self.assertNotIn("RENEW", guard)
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
        self.output = base / "github_output"
        self.output.write_text("", encoding="utf-8")

    def run_step(self):
        script = dict(steps())["Commit the signed files to the branch"].split("run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        env = {**os.environ, "CHANNEL": "beta", "SOURCE_COMMIT": self.source, "GITHUB_OUTPUT": str(self.output)}
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.work, env=env, capture_output=True, text=True)

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

    def test_the_mirror_build_is_not_started_after_a_stand_down(self):
        self.assertIn("steps.push.outputs.pushed != 'false'", dict(steps())["Start the Pages mirror build"])


if __name__ == "__main__":
    unittest.main()
