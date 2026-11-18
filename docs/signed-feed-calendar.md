# The signed feed's re-sign calendar

Each channel (`main` and `beta`) has a signed feed under `signed/<channel>/`: `catalog.json` and
its detached signature `catalog.json.sig`. A signature is valid for 90 days. This page says how it
is renewed on time and what happens when it is not.

## The rhythm

The workflow `signature-expiry.yml` runs once a day. It signs nothing and holds no key. It reads
`expires` of each channel's signed file and acts on the days left:

| Days left | What the check does |
| --- | --- |
| more than 45 | nothing |
| 45 or fewer | starts a renewal run for the channel, unless a publish or renewal run for it is already waiting for approval, queued or running (then it only reports) |
| 14 or fewer, or expired | the same, and the check job fails |

A renewal is `publish-signed.yml` started with `renew` set. It keeps the published body,
`source_commit` included, and only restamps `signed_version`, `not_before` and `expires`. It waits
in the `catalog-signing` environment for the owner's approval like any other signing run, so a
routine re-sign arrives about every 45 days. A renewal stands down, and signs nothing, when the
branch has moved on in anything but `signed/**` or already holds a newer signed pair.

A channel that has no signed file yet is skipped: there is nothing to renew and no signature that
can run out. A signed file that exists but cannot be read counts as an alarm, and so does a signed
branch that cannot be found.

To decide whether a run is already on its way, the check asks GitHub for the signing workflow's
waiting, pending, queued, requested and running runs, every page of each. If any of those queries
fails, the list is incomplete and the check starts nothing that day, so it cannot start a duplicate. The
statuses are asked in the order a run moves through them (requested, pending, queued, waiting, running),
so a run approved between two queries is still seen, and the whole listing is read twice: if the run ids
differ between the two reads, the list counts as incomplete too.
The alarm still works from the signed files alone.

Two behaviours to know about:

- A renewal that is waiting for approval holds the channel's signing slot. A push to that branch
  queues behind it, and its own approval request appears once the renewal is approved (the renewal
  then stands down) or rejected.
- A renewal that is rejected, fails or times out is requested again the next day, once per channel
  per day. Every request still needs the owner's approval, so nothing is signed unattended.

## The alarm

From 14 days before expiry the check job fails every day until a fresh signature lands. To clear
it, approve the waiting renewal run, or start one (Actions, Publish signed feed, Run workflow,
`renew` ticked, on the branch that is due).

GitHub sends the failure mail of a scheduled workflow to one person: the user who last changed the
`cron` line in the workflow file, or, if the workflow was disabled and enabled again, the user who
enabled it. Nobody else is told. So:

- Keep the `cron` line of `signature-expiry.yml` last changed by the account that should be told, and
  let nobody else (a bot included) disable and re-enable the workflow.
- In that account's notification settings (Settings, Notifications, System, Actions), turn on
  notifications for failed workflows.
- To check who a scheduled run would notify, look at the actor of one:
  `gh api "repos/LavX/bazarr-provider-catalog/actions/workflows/signature-expiry.yml/runs?event=schedule&per_page=5" --jq '.workflow_runs[] | [.created_at, .actor.login, .triggering_actor.login]'`.
  A manual run proves nothing about this, because it notifies whoever started it, and a healthy
  check does not fail on a manual run anyway.

## GitHub's 60 day switch-off

GitHub turns scheduled workflows off in a public repository after 60 days without activity. The
renewal cadence has to stay clear of that cut-off, so:

- A renewal is requested at 45 days left. The sign commit of each renewal is repository activity and
  lands about 45 days after the one before it, well inside 60 days. It cannot land sooner than the
  owner approves the run.
- The check fails when the newest commit on the signed branches is 50 days old, ten days before the
  cut-off. It does this only once something has been signed. GitHub does not say whether a commit on
  a branch other than the default one counts as activity, so treat the alarm as a margin and not as
  proof.
- The check lives in its own workflow file, so a disabled schedule never affects the publish
  workflow.

The check does not enable or disable any workflow itself. Re-enabling from inside a job is a known
way around the inactivity policy, it would move the failure mail to the bot that did it, and it
would undo a deliberate disable of the publish workflow.

One gap is left: before the first signing there is no signature to run out, so the 50 day alarm is
off. A repository with no commit for 60 days in that stretch loses its schedule without a word. The
first signing ends the gap, so sign soon after the check is on `main`, or keep the repository
active until then.

If the check is switched off anyway, nothing reports it. That is why the owner also keeps two
calendar entries outside GitHub.

## Rollout

The check does nothing until it is on the default branch: a schedule, and a manual run, start only
from `main`, and `sdk/resign.py` and the `check-expiry` command have to be there too. Until this
workflow reaches `main`, a signed `beta` can age with no renewal request and no alarm, and only the
printed calendar dates protect it. Promote `beta` to `main` with this check, then run it once by
hand (Run workflow) and read its summary table.

## Calendar entries outside GitHub

When a signing run is approved, its summary prints two dates: 30 and 14 days before the new
`expires`, both UTC dates. Put both in a calendar that does not depend on GitHub. On each of those
dates, look at the Actions tab: a renewal should be signed or waiting.

## What a lapse does

When a channel's signature has expired:

- hosts that read the signed feed block installs and updates from that channel and report
  "catalog expired";
- plugins that are already installed keep running;
- the catalog the image carries still serves, because its expiry is ignored in the baked case;
- hosts that never latched the signed feed keep reading the legacy `catalog.json`, which is not
  signed and does not expire.

The lapse ends with the next signature that lands on the channel.
