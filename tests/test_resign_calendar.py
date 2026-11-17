"""The re-sign calendar: renewal at 45 days, alarm at 14, and never before the first signed file exists."""

import base64
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from sdk import cli, resign

HAVE_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
if HAVE_CRYPTOGRAPHY:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from sdk import host_signing, signing

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 12, 1, 6, 17, tzinfo=timezone.utc)
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def body_expiring_in(days, channel="beta"):
    expires = NOW + timedelta(days=days)
    return json.dumps({"channel": channel, "expires": expires.strftime(STAMP), "source_commit": "a" * 40}).encode()


class AssessTests(unittest.TestCase):
    def test_the_state_follows_the_days_left(self):
        for days, state, request, alarm in (
            (89, resign.VALID, False, False),
            (60, resign.VALID, False, False),
            (45.5, resign.VALID, False, False),
            (45, resign.RENEWAL_DUE, True, False),
            (44, resign.RENEWAL_DUE, True, False),
            (30, resign.RENEWAL_DUE, True, False),
            (15, resign.RENEWAL_DUE, True, False),
            (14.5, resign.RENEWAL_DUE, True, False),
            (14, resign.ALARM, True, True),
            (13, resign.ALARM, True, True),
            (1, resign.ALARM, True, True),
            (0, resign.EXPIRED, True, True),
            (-3, resign.EXPIRED, True, True),
        ):
            with self.subTest(days=days):
                result = resign.assess("beta", body_expiring_in(days), now=NOW)
                self.assertEqual(result.state, state)
                self.assertEqual(result.request, request)
                self.assertEqual(result.alarm, alarm)
                self.assertAlmostEqual(result.days_left, days, places=3)

    def test_a_channel_with_no_signed_file_is_skipped_and_never_alarms(self):
        result = resign.assess("main", None, now=NOW)
        self.assertEqual(result.state, resign.ABSENT)
        self.assertFalse(result.request)
        self.assertFalse(result.alarm)
        self.assertIsNone(result.expires)

    def test_a_signed_file_that_cannot_be_read_is_an_alarm_without_a_renewal_request(self):
        for body in (b"not json", b"[]", b"{}", b'{"expires": 5}', b'{"expires": "tomorrow"}',
                     b'{"expires": "2027-01-01 00:00:00"}', b""):
            with self.subTest(body=body):
                result = resign.assess("beta", body, now=NOW)
                self.assertEqual(result.state, resign.UNREADABLE)
                self.assertTrue(result.alarm)
                self.assertFalse(result.request)

    def test_a_run_on_its_way_stops_the_request_but_not_the_alarm(self):
        for run in (resign.RUN_ACTIVE, resign.RUN_UNKNOWN):
            with self.subTest(run=run):
                due = resign.assess("beta", body_expiring_in(20), now=NOW, run=run)
                self.assertFalse(due.request)
                self.assertFalse(due.alarm)
                near = resign.assess("beta", body_expiring_in(13), now=NOW, run=run)
                self.assertFalse(near.request)
                self.assertTrue(near.alarm)

    def test_the_policy_numbers(self):
        self.assertEqual(resign.RENEW_WITHIN_DAYS, 45)
        self.assertEqual(resign.ALARM_WITHIN_DAYS, 14)
        self.assertEqual(resign.REMINDER_DAYS, (30, 14))
        # 90 days of signature, renewed at 45 days left: about every 45 days, so a sign commit (repository
        # activity) lands well inside the 60 days after which GitHub turns a schedule off, and the quiet alarm
        # fires before that cut-off.
        self.assertEqual(signing_validity() - resign.RENEW_WITHIN_DAYS, 45)
        self.assertLess(signing_validity() - resign.RENEW_WITHIN_DAYS, 60)
        self.assertLess(resign.QUIET_ALARM_DAYS, 60)

    def test_the_reminder_dates_are_thirty_and_fourteen_days_before_expiry(self):
        expires = datetime(2027, 3, 1, 6, 17, tzinfo=timezone.utc)
        self.assertEqual(resign.reminder_dates(expires), (date(2027, 1, 30), date(2027, 2, 15)))


def signing_validity():
    from sdk.signing import DEFAULT_VALIDITY_DAYS

    return DEFAULT_VALIDITY_DAYS


class ActiveRunTests(unittest.TestCase):
    def test_every_unfinished_publish_or_renewal_run_counts(self):
        runs = [
            {"status": "waiting", "headBranch": "beta", "event": "push"},
            {"status": "pending", "headBranch": "main", "event": "workflow_dispatch"},
            {"status": "in_progress", "headBranch": "main", "event": "push"},
            {"status": "queued", "headBranch": "feature", "event": "push"},
        ]
        self.assertEqual(resign.active_channels(runs), {"beta", "main", "feature"})

    def test_finished_runs_and_the_scheduled_check_itself_do_not(self):
        runs = [
            {"status": "completed", "headBranch": "beta", "event": "push"},
            {"status": "in_progress", "headBranch": "main", "event": "schedule"},
            {"status": "waiting", "headBranch": "beta", "event": "schedule"},
            "junk",
            {"status": "waiting", "event": "push"},
        ]
        self.assertEqual(resign.active_channels(runs), set())

    def test_an_unreadable_list_is_unknown_not_empty(self):
        self.assertIsNone(resign.active_channels(None))
        self.assertIsNone(resign.active_channels({"status": "waiting"}))
        self.assertEqual(resign.active_channels([]), set())


class LoadRunsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "runs"

    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        return resign.load_runs(self.path)

    def test_a_json_list_and_json_lines_both_read(self):
        run = {"status": "waiting", "headBranch": "beta", "event": "workflow_dispatch"}
        self.assertEqual(self.load(json.dumps([run, run])), [run, run])
        self.assertEqual(self.load(json.dumps(run) + "\n" + json.dumps(run) + "\n"), [run, run])

    def test_a_file_with_no_run_is_an_empty_list_and_a_missing_one_is_unknown(self):
        self.assertEqual(self.load(""), [])
        self.assertEqual(self.load("[]"), [])
        self.assertIsNone(resign.load_runs(self.path.with_name("missing")))
        self.assertIsNone(resign.load_runs(None))

    def test_a_cut_off_or_wrong_file_is_unknown(self):
        self.assertIsNone(self.load('{"status": "wa'))
        self.assertIsNone(self.load('[{"status": "waiting"}'))
        self.assertIsNone(self.load('{"status": "waiting"}\n{"status'))


class CheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def put(self, channel, body):
        path = self.root / "signed" / channel / "catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["check-expiry", str(self.root), "--now", NOW.strftime(STAMP), *args])
        return code, out.getvalue(), err.getvalue()

    def outputs(self, *args, runs=None):
        outputs = self.root / "github_output"
        extra = ["--outputs-file", str(outputs)]
        if runs is not None:
            path = self.root / "runs.json"
            path.write_text(json.dumps(runs), encoding="utf-8")
            extra += ["--runs", str(path)]
        code, out, err = self.run_cli(*extra, *args)
        values = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines())
        return code, out, err, values

    def test_nothing_is_signed_yet_so_nothing_alarms_or_renews(self):
        code, out, err, values = self.outputs("--fail-on-alarm", runs=[])
        self.assertEqual(code, 0, err)
        self.assertEqual(values, {"request": "", "alarm": "false"})
        self.assertEqual(out.count("skipped, never signed"), 2)
        self.assertEqual(err, "")

    def test_one_channel_signed_and_the_other_not_yet(self):
        self.put("beta", body_expiring_in(80))
        code, _, _, values = self.outputs("--fail-on-alarm", runs=[])
        self.assertEqual(code, 0)
        self.assertEqual(values, {"request": "", "alarm": "false"})
        # The unsigned channel stays quiet even while the signed one is due.
        self.put("beta", body_expiring_in(29))
        _, _, _, values = self.outputs(runs=[])
        self.assertEqual(values, {"request": "beta", "alarm": "false"})

    def test_a_fixture_expiring_in_44_days_requests_a_renewal(self):
        self.put("beta", body_expiring_in(44))
        code, out, err, values = self.outputs("--fail-on-alarm", runs=[])
        self.assertEqual(code, 0, err)
        self.assertEqual(values, {"request": "beta", "alarm": "false"})
        self.assertIn("renewal requested", out)

    def test_a_fixture_expiring_in_13_days_fails_the_check_and_still_requests(self):
        self.put("beta", body_expiring_in(13))
        code, out, err, values = self.outputs("--fail-on-alarm", runs=[])
        self.assertEqual(code, 1)
        self.assertEqual(values, {"request": "beta", "alarm": "true"})
        self.assertIn("::error::beta", err)
        # The table goes to the run summary; the error annotation does not.
        self.assertNotIn("::error::", out)
        # Without the flag the command reports through its outputs and exits cleanly.
        code, _, _, _ = self.outputs(runs=[])
        self.assertEqual(code, 0)

    def test_the_fixtures_at_45_30_14_and_0_days(self):
        for days, request, alarm in ((50, "", "false"), (45, "beta", "false"), (30, "beta", "false"),
                                     (14, "beta", "true"), (0, "beta", "true")):
            with self.subTest(days=days):
                self.put("beta", body_expiring_in(days))
                _, _, _, values = self.outputs(runs=[])
                self.assertEqual(values, {"request": request, "alarm": alarm})

    def test_a_waiting_run_means_nothing_new_is_started_and_the_check_only_reports(self):
        runs = [{"status": "waiting", "headBranch": "beta", "event": "workflow_dispatch"}]
        self.put("beta", body_expiring_in(29))
        self.put("main", body_expiring_in(20, "main"))
        code, out, _, values = self.outputs("--fail-on-alarm", runs=runs)
        self.assertEqual(code, 0)
        self.assertEqual(values, {"request": "main", "alarm": "false"}, "only the channel without a run is requested")
        self.assertIn("reported only", out)
        self.put("beta", body_expiring_in(10))
        code, _, _, values = self.outputs("--fail-on-alarm", runs=runs)
        self.assertEqual(code, 1, "the alarm still fires while a run waits")
        self.assertEqual(values["request"], "main")

    def test_a_run_list_that_cannot_be_read_starts_nothing(self):
        self.put("beta", body_expiring_in(29))
        for runs_file in (None, "missing.json", "broken.json"):
            with self.subTest(runs_file=runs_file):
                (self.root / "broken.json").write_text("{", encoding="utf-8")
                extra = ["--runs", str(self.root / runs_file)] if runs_file else []
                outputs = self.root / "github_output"
                outputs.write_text("", encoding="utf-8")
                code, out, _ = self.run_cli("--outputs-file", str(outputs), *extra)
                self.assertEqual(code, 0)
                self.assertEqual(outputs.read_text(encoding="utf-8"), "request=\nalarm=false\n")
                self.assertIn("the run list could not be read", out)

    def test_a_channel_flag_limits_the_check(self):
        self.put("beta", body_expiring_in(5))
        self.put("main", body_expiring_in(5, "main"))
        _, _, _, values = self.outputs("--channel", "main", runs=[])
        self.assertEqual(values["request"], "main")


class GitRefTests(unittest.TestCase):
    """The check job reads each channel's file from its own branch of one checkout."""

    def git(self, *args, env=None):
        return subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=self.repo, check=True, capture_output=True, text=True, env={**os.environ, **(env or {})},
        ).stdout

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q")
        (self.repo / "README.md").write_text("one\n", encoding="utf-8")
        self.git("add", ".")
        self.commit("first", NOW - timedelta(days=100))

    def commit(self, message, when):
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+0000")
        self.git("commit", "-q", "-m", message, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})

    def branch_with_signed_file(self, channel, body, when=None):
        self.git("checkout", "-q", "-B", channel)
        path = self.repo / "signed" / channel / "catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.git("add", ".")
        self.commit(f"chore(catalog): sign {channel}", when or NOW - timedelta(days=1))
        self.git("checkout", "-q", "main")

    def test_a_branch_without_a_signed_file_is_absent(self):
        self.git("branch", "beta")
        self.assertIsNone(resign.read_signed_body(self.repo, "main", git_ref_prefix=""))
        found = resign.check(self.repo, ("main", "beta"), now=NOW, runs=[], git_ref_prefix="")
        self.assertEqual([item.state for item in found], [resign.ABSENT, resign.ABSENT])
        self.assertFalse(any(item.alarm or item.request for item in found))

    def test_a_branch_that_does_not_exist_is_an_error_and_not_never_signed(self):
        with self.assertRaises(cli.CatalogError):
            resign.read_signed_body(self.repo, "beta", git_ref_prefix="")
        with self.assertRaises(cli.CatalogError):
            resign.read_signed_body(self.repo, "main", git_ref_prefix="origin/")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["check-expiry", str(self.repo), "--git-ref-prefix", "origin/", "--now", NOW.strftime(STAMP)])
        self.assertEqual(code, 1)
        self.assertIn("error:", err.getvalue())

    def test_a_signed_file_on_one_branch_is_judged_without_touching_the_other(self):
        self.git("branch", "beta")
        self.branch_with_signed_file("beta", body_expiring_in(20))
        found = {item.channel: item for item in resign.check(self.repo, ("main", "beta"), now=NOW, runs=[], git_ref_prefix="")}
        self.assertEqual(found["main"].state, resign.ABSENT)
        self.assertEqual(found["beta"].state, resign.RENEWAL_DUE)
        self.assertTrue(found["beta"].request)
        self.assertEqual(self.git("status", "--short"), "")
        self.assertEqual(self.git("branch", "--show-current").strip(), "main")

    def test_a_remote_tracking_prefix(self):
        self.branch_with_signed_file("beta", body_expiring_in(20))
        self.git("update-ref", "refs/remotes/origin/beta", "beta")
        body = resign.read_signed_body(self.repo, "beta", git_ref_prefix="origin/")
        self.assertEqual(json.loads(body)["channel"], "beta")

    def run_check(self, *args):
        out, err = io.StringIO(), io.StringIO()
        outputs = self.repo.parent / (self.repo.name + "-outputs")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["check-expiry", str(self.repo), "--git-ref-prefix", "", "--now", NOW.strftime(STAMP),
                             "--outputs-file", str(outputs), "--fail-on-alarm", *args])
        values = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines())
        return code, out.getvalue(), err.getvalue(), values

    def test_a_quiet_repository_alarms_before_the_sixty_day_cut_off_once_something_is_signed(self):
        self.branch_with_signed_file("beta", body_expiring_in(80), when=NOW - timedelta(days=51))
        self.git("checkout", "-q", "-B", "main", "beta")
        code, out, err, values = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(values["alarm"], "true")
        self.assertIn("no commit for 51 days", err)
        self.assertIn("51.0 days ago", out)

    def test_activity_inside_fifty_days_does_not_alarm(self):
        self.branch_with_signed_file("beta", body_expiring_in(80), when=NOW - timedelta(days=49))
        self.git("checkout", "-q", "-B", "main", "beta")
        code, _, err, values = self.run_check()
        self.assertEqual((code, values["alarm"], err), (0, "false", ""))

    def test_a_quiet_repository_with_nothing_signed_yet_does_not_alarm(self):
        self.git("branch", "beta")
        code, out, err, values = self.run_check()
        self.assertEqual((code, values, err), (0, {"request": "", "alarm": "false"}, ""))
        self.assertNotIn("days ago", out)

    def test_the_newest_commit_on_either_branch_counts(self):
        self.branch_with_signed_file("beta", body_expiring_in(80), when=NOW - timedelta(days=70))
        self.git("checkout", "-q", "main")
        (self.repo / "other.txt").write_text("x\n", encoding="utf-8")
        self.git("add", ".")
        self.commit("later", NOW - timedelta(days=2))
        code, _, _, values = self.run_check()
        self.assertEqual((code, values["alarm"]), (0, "false"))


@unittest.skipUnless(HAVE_CRYPTOGRAPHY, "needs the cryptography package (sdk/requirements-signing.txt)")
class RenewalRoundTripTests(unittest.TestCase):
    """A real signed pair ages until the check asks for a renewal, which restamps it for another 90 days."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        def raw(key):
            return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

        root_key, self.ci_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        root_id = signing.key_id(raw(root_key))
        self.roots = {root_id: raw(root_key)}
        keys = [{
            "key_id": signing.key_id(raw(self.ci_key)), "algorithm": "ed25519",
            "public_key": base64.b64encode(raw(self.ci_key)).decode(), "added": "2026-11-17",
        }]
        statement = host_signing.key_file_statement(root_id, 1, keys, [])
        (self.root / "keys").mkdir()
        (self.root / "keys/catalog-signing-keys.json").write_text(json.dumps({
            "key_file_version": 1, "root_key_id": root_id, "keys": keys, "revocations": [],
            "signature": base64.b64encode(root_key.sign(statement)).decode(),
        }), encoding="utf-8")
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(ROOT / "providers/smoke", self.root / "providers/smoke", ignore=ignore)
        shutil.copytree(ROOT / "tests/fixtures/plugins/fixture_translator", self.root / "plugins/fixture_translator", ignore=ignore)
        self.signed_at = NOW
        signing.sign(self.root, "beta", self.ci_key, now=self.signed_at, source_commit="d" * 40, roots=self.roots)

    def assess(self, days_after_signing, run=resign.NO_RUN):
        body = (self.root / "signed/beta/catalog.json").read_bytes()
        return resign.assess("beta", body, now=self.signed_at + timedelta(days=days_after_signing), run=run)

    def test_a_signature_is_valid_for_90_days_and_due_for_renewal_from_day_45(self):
        body = json.loads((self.root / "signed/beta/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(body["expires"], (self.signed_at + timedelta(days=90)).strftime(STAMP))
        self.assertEqual(self.assess(44).state, resign.VALID)
        self.assertEqual(self.assess(45).state, resign.RENEWAL_DUE)
        self.assertEqual(self.assess(77).state, resign.ALARM)

    def test_the_renewal_the_check_asks_for_keeps_the_body_and_starts_a_new_90_days(self):
        before = json.loads((self.root / "signed/beta/catalog.json").read_text(encoding="utf-8"))
        asked_at = self.signed_at + timedelta(days=61)
        self.assertTrue(self.assess(61).request)
        # The renew path: the same body, a later signed_version and fresh dates.
        signing.sign(self.root, "beta", self.ci_key, now=asked_at, renew=True, roots=self.roots)
        after = json.loads((self.root / "signed/beta/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(after["source_commit"], before["source_commit"])
        self.assertEqual(after["providers"], before["providers"])
        self.assertEqual(after["plugins"], before["plugins"])
        self.assertGreater(after["signed_version"], before["signed_version"])
        self.assertEqual(after["expires"], (asked_at + timedelta(days=90)).strftime(STAMP))
        self.assertTrue(signing.verify_files(self.root, "beta", now=asked_at, roots=self.roots).ok)
        renewed = resign.assess("beta", (self.root / "signed/beta/catalog.json").read_bytes(), now=asked_at)
        self.assertEqual(renewed.state, resign.VALID)
        self.assertFalse(renewed.request or renewed.alarm)

    def test_the_sign_command_prints_the_two_reminder_dates(self):
        key = self.ci_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode("ascii")
        out, err = io.StringIO(), io.StringIO()
        now = self.signed_at
        with mock.patch.dict(os.environ, {"TEST_CALENDAR_KEY": key}), mock.patch.object(signing, "ROOTS", self.roots), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([
                "sign", str(self.root), "--channel", "beta", "--key-env", "TEST_CALENDAR_KEY",
                "--renew", "--now", (now + timedelta(days=1)).strftime(STAMP),
            ])
        self.assertEqual(code, 0, err.getvalue())
        expires = now + timedelta(days=1 + 90)
        renew_by, alarm_by = resign.reminder_dates(expires)
        self.assertIn(
            f"calendar: add two reminders outside GitHub, on {renew_by.isoformat()} (30 days before expiry) "
            f"and {alarm_by.isoformat()} (14 days before expiry), both UTC dates",
            out.getvalue(),
        )
        self.assertEqual((expires.date() - renew_by).days, 30)
        self.assertEqual((expires.date() - alarm_by).days, 14)


if __name__ == "__main__":
    unittest.main()
