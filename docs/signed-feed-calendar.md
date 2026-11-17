# The signed feed's re-sign calendar

Each channel (`main` and `beta`) has a signed feed under `signed/<channel>/`: `catalog.json` and
its detached signature `catalog.json.sig`. A signature is valid for 90 days. This page says how it
is renewed on time and what happens when it is not.

## The rhythm

The workflow `publish-signed.yml` has a daily scheduled check. It signs nothing and holds no key.
It reads `expires` of each channel's signed file and acts on the days left:

| Days left | What the check does |
| --- | --- |
| more than 30 | nothing |
| 30 or fewer | starts a renewal run for the channel, unless a publish or renewal run for it is already waiting for approval or running (then it only reports) |
| 14 or fewer, or expired | the same, and the check job fails |

A renewal is the workflow started with `renew` set. It keeps the published body, its
`source_commit` included, and only restamps `signed_version`, `not_before` and `expires`. It waits
in the `catalog-signing` environment for the owner's approval like any other signing run, so a
routine re-sign arrives about every 60 days. A renewal stands down, and signs nothing, when the
branch has moved on in anything but `signed/**` or already holds a newer signed pair.

A channel that has no signed file yet is skipped: there is nothing to renew and no signature that
can run out. A signed file that exists but cannot be read counts as an alarm.

## The alarm

From 14 days before expiry the check job fails every day until a fresh signature lands. GitHub
e-mails the failure to the repository owner. To clear it, approve the waiting renewal run, or start
one (Actions, Publish signed feed, Run workflow, `renew` ticked, on the branch that is due).

If the daily check cannot read the list of workflow runs, it starts nothing that day, so it cannot
start a duplicate, and the alarm still works from the files alone.

## Calendar entries outside GitHub

GitHub turns scheduled workflows off in a public repository after 60 days without activity. The
sign commits are repository activity and recur at most 60 days apart, but that margin is thin, so
the daily check is not the only reminder. When a signing run is approved, its summary prints two
dates: 30 days and 14 days before the new `expires`. Put both in a calendar that does not depend on
GitHub. A schedule runs only from the default branch, so it starts working once this workflow has
reached it.

## What a lapse does

When a channel's signature has expired:

- hosts that read the signed feed block installs and updates from that channel and report
  "catalog expired";
- plugins that are already installed keep running;
- the catalog the image carries still serves, because its expiry is ignored in the baked case;
- hosts that never latched the signed feed keep reading the legacy `catalog.json`, which is not
  signed and does not expire.

The lapse ends with the next signature that lands on the channel.
