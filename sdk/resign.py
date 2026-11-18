"""The re-sign calendar: when a channel's signature needs renewing, and when it is an alarm.

A signature lasts 90 days (``signing.DEFAULT_VALIDITY_DAYS``). A daily check reads ``expires`` of
each channel's signed body and decides, from the time left:

- more than 45 days: nothing to do;
- 45 days or fewer: ask for a renewal, unless a publish or renewal run for that channel is
  already waiting for approval or running (the check only reports then);
- 14 days or fewer, or already expired: alarm. The check job fails, which GitHub e-mails, and it
  keeps failing daily until a fresh signature lands.

A renewal restamps the signed body that is already published (``sdk sign --renew``), so a routine
re-sign arrives about every 45 days. That is well inside the 60 days after which GitHub turns the
scheduled check off for a repository with no activity, because the sign commit is the activity. The
check also raises the alarm itself when the repository has been quiet for 50 days.

A channel that has no signed file yet is skipped. Nothing was ever signed for it, so no host can
hold a signature of it that runs out, and there is nothing to renew. A signed file that exists but
cannot be read is an alarm, because it is a real fault.

This module needs only the standard library: the check job holds no key and installs nothing. The
body it reads is not verified here. It is an alarm and cannot make a stale signature look fresh to
a host, which does verify.
"""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cli
from .signing import BODY_NAME, SIGNED_DIR, TIMESTAMP_FORMAT

# Days left at or below which a renewal is requested, and at or below which the check fails.
RENEW_WITHIN_DAYS = 45
ALARM_WITHIN_DAYS = 14
# The two calendar entries the owner keeps outside GitHub, in days before expiry.
REMINDER_DAYS = (30, 14)
# GitHub turns scheduled workflows off in a public repository after 60 days without activity. The check
# fails once the newest commit of a signed channel is this old, ten days before that.
QUIET_ALARM_DAYS = 50

# What a channel can be in, in the order of increasing urgency.
ABSENT = "absent"
VALID = "valid"
RENEWAL_DUE = "renewal due"
ALARM = "alarm"
EXPIRED = "expired"
UNREADABLE = "unreadable"

# Whether a publish or renewal run for the channel is already on its way.
NO_RUN = "none"
RUN_ACTIVE = "waiting or running"
RUN_UNKNOWN = "unknown"

# Workflow runs that count as "already on its way". Only completed runs do not.
_FINISHED = "completed"


@dataclass(frozen=True)
class Assessment:
    channel: str
    state: str
    expires: datetime | None
    days_left: float | None
    run: str
    detail: str

    @property
    def request(self):
        """Whether a renewal run should be started for this channel now."""
        return self.state in (RENEWAL_DUE, ALARM, EXPIRED) and self.run == NO_RUN

    @property
    def alarm(self):
        return self.state in (ALARM, EXPIRED, UNREADABLE)


def reminder_dates(expires):
    """The two calendar days (UTC) the owner notes outside GitHub: 30 and 14 days before ``expires``."""
    first, second = REMINDER_DAYS
    return ((expires - timedelta(days=first)).date(), (expires - timedelta(days=second)).date())


def _expires_of(body):
    value = body.get("expires")
    if not isinstance(value, str):
        raise ValueError("the signed body has no expires")
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"expires is not YYYY-MM-DDTHH:MM:SSZ: {value!r}") from exc


def assess(channel, body_bytes, *, now, run=NO_RUN):
    """Judge one channel. ``body_bytes`` is its signed body, or None when it has no signed file."""
    if body_bytes is None:
        return Assessment(channel, ABSENT, None, None, run, "no signed file yet, nothing to renew")
    try:
        body = json.loads(body_bytes)
        if not isinstance(body, dict):
            raise ValueError("the signed body is not a JSON object")
        expires = _expires_of(body)
    except ValueError as exc:
        return Assessment(channel, UNREADABLE, None, None, run, f"the signed file cannot be read: {exc}")
    days_left = (expires - now).total_seconds() / 86400
    if days_left <= 0:
        state, detail = EXPIRED, "the signature has expired"
    elif days_left <= ALARM_WITHIN_DAYS:
        state, detail = ALARM, f"the signature runs out in {days_left:.1f} days"
    elif days_left <= RENEW_WITHIN_DAYS:
        state, detail = RENEWAL_DUE, f"the signature runs out in {days_left:.1f} days"
    else:
        state, detail = VALID, f"{days_left:.1f} days left"
    return Assessment(channel, state, expires, days_left, run, detail)


def active_channels(runs):
    """Branches that have a publish or renewal run waiting for approval, queued or running.

    ``runs`` is the list of unfinished runs of the publish workflow (``status``, ``headBranch`` and
    ``event`` of each). ``None``, or anything that is not a list, means the list could not be
    fetched whole, and then nobody can say, so it returns None.
    """
    if not isinstance(runs, list):
        return None
    active = set()
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("status") == _FINISHED:
            continue
        branch = run.get("headBranch")
        if isinstance(branch, str):
            active.add(branch)
    return active


def load_runs(path):
    """The run list in ``path``, or None when it is missing or not whole.

    The file is a JSON list, or one JSON object per line (what a paginated listing writes). A file that
    exists and holds no run is an empty list: nothing is waiting. Anything unreadable is None.
    """
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        if text.lstrip().startswith("["):
            runs = json.loads(text)
            return runs if isinstance(runs, list) else None
        runs = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        return None
    return runs


def _git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise cli.CatalogError(f"git {' '.join(args)} failed: {detail}") from exc


def read_signed_body(root, channel, *, git_ref_prefix=None):
    """The channel's signed body as bytes, or None when the channel has no signed file.

    With ``git_ref_prefix`` (for example ``origin/``) the file is read from that branch of the
    checkout instead of the working tree, so one job can look at every channel. A branch that does
    not exist is an error, not "never signed": a wrong prefix or a failed fetch must not pass quietly.
    """
    relative = f"{SIGNED_DIR}/{channel}/{BODY_NAME}"
    if git_ref_prefix is None:
        path = Path(root) / relative
        return path.read_bytes() if path.is_file() else None
    ref = f"{git_ref_prefix}{channel}"
    _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if not _git(root, "ls-tree", "--name-only", ref, "--", relative).strip():
        return None
    return _git(root, "show", f"{ref}:{relative}")


def quiet_days(root, channels, *, now, git_ref_prefix=""):
    """Days since the newest commit on any of the channels' branches, or None when there is no such branch."""
    newest = None
    for channel in channels:
        ref = f"{git_ref_prefix}{channel}"
        out = _git(root, "log", "-1", "--format=%ct", ref).decode().strip()
        if out:
            stamp = datetime.fromtimestamp(int(out), tz=timezone.utc)
            newest = stamp if newest is None or stamp > newest else newest
    return None if newest is None else (now - newest).total_seconds() / 86400


def render(assessments):
    """A markdown table for the run summary."""
    lines = ["| channel | state | expires | days left | renewal run | action |", "| --- | --- | --- | --- | --- | --- |"]
    for item in assessments:
        expires = item.expires.strftime(TIMESTAMP_FORMAT) if item.expires else "-"
        days = f"{item.days_left:.1f}" if item.days_left is not None else "-"
        if item.state == ABSENT:
            run, action = "-", "skipped, never signed"
        elif item.state == UNREADABLE:
            run, action = item.run, item.detail
        elif item.state == VALID:
            run, action = item.run, "none"
        elif item.run == RUN_ACTIVE:
            run, action = item.run, "reported only, a run is already on its way"
        elif item.run == RUN_UNKNOWN:
            run, action = item.run, "no run started, the run list could not be read"
        else:
            run, action = item.run, "renewal requested"
        lines.append(f"| {item.channel} | {item.state} | {expires} | {days} | {run} | {action} |")
    return "\n".join(lines) + "\n"


def check(root, channels, *, now, runs, git_ref_prefix=None):
    """Assess every channel. ``runs`` is the parsed run list, or None when it could not be fetched."""
    active = active_channels(runs)
    assessments = []
    for channel in channels:
        body = read_signed_body(root, channel, git_ref_prefix=git_ref_prefix)
        # Without the run list, starting a renewal could duplicate one that is already waiting.
        run = RUN_UNKNOWN if active is None else RUN_ACTIVE if channel in active else NO_RUN
        assessments.append(assess(channel, body, now=now, run=run))
    return assessments
