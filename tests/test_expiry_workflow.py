"""The daily expiry check: its own workflow, no key, complete run listing, and a renewal that takes the renew path."""

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = (ROOT / ".github/workflows/signature-expiry.yml").read_text(encoding="utf-8")
PUBLISH = (ROOT / ".github/workflows/publish-signed.yml").read_text(encoding="utf-8")


def steps():
    block = CHECK.split("    steps:\n", 1)[1]
    parts = re.split(r"(?m)^      - ", block)[1:]
    return [(re.search(r"name: (.*)", part).group(1) if "name:" in part else "uses", part) for part in parts]


def step(name):
    return dict(steps())[name]


class ScheduleTests(unittest.TestCase):
    def test_there_is_one_daily_schedule_in_a_file_of_its_own(self):
        crons = re.findall(r'- cron: "(.*)"', CHECK)
        self.assertEqual(len(crons), 1)
        minute, hour, day, month, weekday = crons[0].split()
        self.assertTrue(minute.isdigit() and hour.isdigit(), "once a day, at a fixed time")
        self.assertEqual((day, month, weekday), ("*", "*", "*"))
        self.assertIn("workflow_dispatch:", CHECK)
        # A schedule that GitHub turns off never touches the publish workflow.
        self.assertNotIn("schedule", PUBLISH)

    def test_the_check_holds_no_key_and_never_waits_for_the_environment(self):
        self.assertNotRegex(CHECK, r"(?m)^\s+environment:")
        for forbidden in ("secrets.", "CATALOG_SIGNING_KEY", "--key-env", "sdk sign", "git push", "git commit",
                          "requirements-signing", "git checkout", "git merge", "git pull", "git switch", "git rebase", "git reset"):
            self.assertNotIn(forbidden, CHECK, forbidden)
        self.assertIn("github.repository == 'LavX/bazarr-provider-catalog'", CHECK)

    def test_the_check_may_start_a_run_but_not_write_the_repository(self):
        permissions = CHECK.split("    permissions:\n", 1)[1].split("    env:", 1)[0]
        self.assertEqual(re.findall(r"(\w+): (\w+)", permissions), [("contents", "read"), ("actions", "write")])

    def test_it_has_a_concurrency_group_that_the_signing_runs_do_not_share(self):
        self.assertIn("group: signature-expiry", CHECK)
        self.assertNotIn("signature-expiry", PUBLISH)

    def test_it_does_not_enable_or_disable_any_workflow(self):
        # Re-enabling a workflow from inside a job hides the inactivity switch-off, reroutes failure mail to the
        # bot and undoes a deliberate disable of the publish workflow. The check does none of it.
        self.assertNotIn("gh workflow enable", CHECK)
        self.assertNotIn("gh workflow disable", CHECK)
        self.assertNotIn("/enable", CHECK)
        self.assertNotIn("Keep the schedule enabled", CHECK)
        self.assertNotIn("circumvent", CHECK.lower())
        self.assertIn("sign commit", CHECK)
        self.assertIn("50 days", CHECK)


class RunListTests(unittest.TestCase):
    def test_it_queries_only_unfinished_runs_of_the_signing_workflow_with_every_page(self):
        listing = step("List the publish runs that are not finished")
        # In the order a run moves through them, so one that moves on between two queries is seen by the later one.
        self.assertIn("for status in requested pending queued waiting in_progress; do", listing)
        self.assertIn("actions/workflows/publish-signed.yml/runs?status=${status}&per_page=100", listing)
        self.assertIn("gh api --paginate", listing)
        self.assertNotIn("gh run list", listing)
        self.assertNotIn("--limit", listing)
        self.assertNotIn("completed", listing)

    def test_an_incomplete_query_leaves_no_file_so_nothing_is_requested(self):
        listing = step("List the publish runs that are not finished")
        self.assertIn('>> "$1" || return 1', listing)
        # The list the check reads exists only after every query succeeded and the two reads agreed.
        self.assertIn('mv "$RUNNER_TEMP/runs.second" "$RUNNER_TEMP/runs.jsonl"', listing)
        self.assertIn('rm -f "$RUNNER_TEMP/runs.jsonl"', listing)
        self.assertLess(listing.index('rm -f "$RUNNER_TEMP/runs.jsonl"'), listing.index("gh api"))
        read = step("Read when each signature runs out")
        self.assertIn('--runs "$RUNNER_TEMP/runs.jsonl"', read)

    def test_the_check_reads_both_channels_without_running_their_code(self):
        fetch = step("Fetch both channels")
        self.assertIn("refs/remotes/origin/main", fetch)
        self.assertIn("refs/remotes/origin/beta", fetch)
        read = step("Read when each signature runs out")
        self.assertIn("python3 -B -m sdk check-expiry --git-ref-prefix origin/", read)
        self.assertIn('--outputs-file "$GITHUB_OUTPUT"', read)
        self.assertIn('tee -a "$GITHUB_STEP_SUMMARY"', read)


class RunListScriptTests(unittest.TestCase):
    """Runs the listing step with a stand-in for gh that keeps a small state of its own."""

    ORDER = ("requested", "pending", "queued", "waiting", "in_progress")

    def run_step(self, fail_on=None, move_after=None, appear_after=None):
        """``move_after``: run 7 waits for approval for that many gh calls, then runs.
        ``appear_after``: run 9 starts waiting after that many calls. ``fail_on``: a status whose query fails."""
        script = step("List the publish runs that are not finished").split("run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "bin").mkdir()
        gh = base / "bin" / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            'echo "$@" >> "$CALLS"\n'
            'n=$(wc -l < "$CALLS")\n'
            'case "$*" in *"status=$FAIL_ON&"*) echo boom >&2; exit 1;; esac\n'
            'status=$(echo "$*" | sed -n "s/.*status=\\([a-z_]*\\)&.*/\\1/p")\n'
            # Run 7 is waiting for approval for MOVE_AFTER calls and then runs.
            'if [ -n "$MOVE_AFTER" ]; then\n'
            '  if [ "$n" -le "$MOVE_AFTER" ]; then now=waiting; else now=in_progress; fi\n'
            '  [ "$status" = "$now" ] && echo "{\\"id\\":7,\\"status\\":\\"$now\\",\\"headBranch\\":\\"beta\\",\\"event\\":\\"workflow_dispatch\\"}"\n'
            "fi\n"
            # Run 9 shows up as waiting after APPEAR_AFTER calls.
            'if [ -n "$APPEAR_AFTER" ] && [ "$n" -gt "$APPEAR_AFTER" ] && [ "$status" = waiting ]; then\n'
            '  echo \'{"id":9,"status":"waiting","headBranch":"main","event":"push"}\'\n'
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        (base / "runner").mkdir()
        env = {
            **os.environ, "PATH": f"{base / 'bin'}:{os.environ['PATH']}", "RUNNER_TEMP": str(base / "runner"),
            "GH_REPO": "LavX/bazarr-provider-catalog", "CALLS": str(base / "calls"), "FAIL_ON": fail_on or "none",
            "MOVE_AFTER": "" if move_after is None else str(move_after),
            "APPEAR_AFTER": "" if appear_after is None else str(appear_after),
        }
        result = subprocess.run(["bash", "-eo", "pipefail", "-c", script], env=env, capture_output=True, text=True)
        calls = (base / "calls").read_text(encoding="utf-8").splitlines()
        return result, base / "runner" / "runs.jsonl", calls

    def ids(self, runs):
        # A run that moves on during the read can show under two statuses, which is the same run.
        return sorted({json.loads(line)["id"] for line in runs.read_text(encoding="utf-8").splitlines()})

    def test_every_unfinished_status_is_queried_in_lifecycle_order_and_the_whole_listing_is_read_twice(self):
        result, runs, calls = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 10)
        statuses = [re.search(r"status=([a-z_]+)&", call).group(1) for call in calls]
        self.assertEqual(statuses, list(self.ORDER) * 2)
        for call in calls:
            self.assertIn("--paginate", call)
        self.assertEqual(runs.read_text(encoding="utf-8"), "")

    def test_the_order_cannot_miss_a_run_that_is_approved_between_two_queries(self):
        # Model of the property, on the order the workflow really uses: a run moves from waiting to running
        # after k queries. It must show in some query for every k.
        listing = step("List the publish runs that are not finished")
        order = re.search(r"for status in ([a-z_ ]+); do", listing).group(1).split()
        self.assertEqual(order, list(self.ORDER))
        for k in range(len(order) + 1):
            seen = [status for index, status in enumerate(order, start=1)
                    if status == ("waiting" if index <= k else "in_progress")]
            self.assertTrue(seen, f"a run approved after {k} queries is missed")
        # The reverse order, which an earlier version used, does miss one.
        reverse = list(reversed(order))
        misses = [k for k in range(len(reverse) + 1)
                  if not [s for index, s in enumerate(reverse, start=1) if s == ("waiting" if index <= k else "in_progress")]]
        self.assertTrue(misses)

    def test_a_run_approved_at_any_moment_of_the_listing_is_in_the_list(self):
        for moved_after in range(0, 12):
            result, runs, calls = self.run_step(move_after=moved_after)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(runs.exists(), moved_after)
            self.assertEqual(self.ids(runs), [7], moved_after)

    def test_a_run_that_appears_or_disappears_between_the_two_reads_leaves_no_list(self):
        result, runs, calls = self.run_step(appear_after=5)  # run 9 shows only in the second read
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(runs.exists())
        self.assertIn("changed while it was read", result.stdout)
        self.assertEqual(len(calls), 10)
        result, runs, _ = self.run_step(appear_after=0)  # in both reads: stable
        self.assertEqual(self.ids(runs), [9])

    def test_one_failed_query_leaves_no_list_at_all(self):
        for status in ("waiting", "in_progress", "requested"):
            result, runs, calls = self.run_step(fail_on=status)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(runs.exists(), status)
            self.assertIn("incomplete", result.stdout)


class RenewalTests(unittest.TestCase):
    def test_the_scheduled_run_asks_for_a_renewal_not_a_new_signing(self):
        request = step("Request a renewal")
        self.assertIn('gh workflow run publish-signed.yml --ref "$channel" -f renew=true', request)
        self.assertIn("steps.expiry.outputs.request != ''", request)
        self.assertIn("CHANNELS: ${{ steps.expiry.outputs.request }}", request)

    def test_a_renewal_dispatch_reaches_the_renew_path_with_its_stand_down_rules(self):
        self.assertIn("renew:", PUBLISH)
        self.assertIn("RENEW: ${{ inputs.renew && 'true' || 'false' }}", PUBLISH)
        self.assertIn("args+=(--renew)", PUBLISH)
        self.assertIn('if [ "$RENEW" = "true" ] && ! git diff --quiet HEAD "$tip" -- "signed/${CHANNEL}"', PUBLISH)
        self.assertIn('if [ "$RENEW" = "true" ] && ! git diff --quiet "$SOURCE_COMMIT" "origin/${CHANNEL}" -- "signed/${CHANNEL}"', PUBLISH)

    def test_the_alarm_step_comes_after_the_request_and_fails_the_job(self):
        names = [name for name, _ in steps()]
        self.assertLess(names.index("Request a renewal"), names.index("Fail while a signature or the schedule is at risk"))
        alarm = step("Fail while a signature or the schedule is at risk")
        self.assertIn("steps.expiry.outputs.alarm == 'true'", alarm)
        self.assertIn("exit 1", alarm)
        for reason in ("expired", "cannot be read", "quiet for 50 days"):
            self.assertIn(reason, alarm)


if __name__ == "__main__":
    unittest.main()
