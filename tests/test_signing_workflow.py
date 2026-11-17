"""The publish workflow keeps the key in one step, runs in the protected environment and cannot loop."""

import re
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


if __name__ == "__main__":
    unittest.main()
