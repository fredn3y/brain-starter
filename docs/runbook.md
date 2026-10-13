# Consultant runbook

How to set up a brain with a client in one session, support them afterwards, and ship fixes. Run
[dry-run-checklist.md](dry-run-checklist.md) on your own Mac before the first client session and
after any big kit change.

## Before the session

Ask for these a few days ahead. Each "no" is a blocker to solve first.

| Check | Why |
|---|---|
| The company has a Claude Team or Enterprise plan and you know who the Owner is | Claude Code is included with every Team plan seat. If the user's seat lacks it, Claude says "You haven't been added to your organization yet" and the Owner must fix the seat (setup prompt A). |
| Claude in Chrome works in the user's Chrome | Setup uses it for Google Cloud and Slack. It's on by default for Team (Organization settings > Claude in Chrome). |
| Mac model, chip and macOS version | Needs macOS 13 or newer. macOS 27 runs on Apple silicon only; macOS 26 is the last release for Intel Macs. |
| Company-managed Mac (MDM)? | IT may block background items, installers or Keychain prompts. Get IT to allow the brain's background item (`local.brain-starter.catchup`) beforehand. |
| Apple's Command Line Tools installed (`xcode-select -p` prints a path) | The `brain` command runs on Apple's Python (3.9). Installing the tools takes a while; do it beforehand with `xcode-select --install`. |
| The user is a Google Workspace admin, or one is on call | The Google sign-in is an Internal app in their company's Google Cloud organization. A personal Gmail account can't do this. |
| The user can install apps in Slack, or an admin can approve one | The Slack app is internal, user-token only, read-only. |
| ChatGPT plan, if the user will use Codex | Business plans and personal plans handle data differently (see [security.md](security.md)). Codex is optional. |
| Time Machine is on, or the user is willing to turn it on | The brain folder holds their notes and tasks. |
| At least 2 GB free disk | Tools, snapshots and logs. |

## The session (75-90 minutes)

| Time | Step | Done when |
|---|---|---|
| 5 min | Explain it: it reads mail, calendar, Drive and Slack, keeps tasks, writes a morning brief, drafts replies. It never sends. | The user can say what it won't do. |
| 10 min | Terminal: run the install line from the README. Watch for the Command Line Tools prompt. | The installer finishes and `brain doctor` runs. |
| 5 min | `cd ~/brain && claude`: the user trusts the folder, logs in (Claude account with subscription, their Team plan, Authorize), types `/exit`. Then `claude auth status --text`. | It shows their login. |
| 5 min | Open T3 Code (Alpha). The welcome wizard checks Claude Code and Codex. Add `~/brain` as a project, start a Claude thread, type "set me up". | The `onboard` skill starts. |
| 15 min | Google: setup prompt B in Claude in Chrome, then `brain google connect`, then `brain google status`. | Gmail, Calendar and Drive show as connected. |
| 10 min | Slack: `brain slack create-app`, setup prompt C, `brain secret set slack --gui`, `brain slack whoami`. | `whoami` shows the user's workspace and name. |
| 15 min | Interview: fills `me.md`, `strategy.md` and the user's settings (timezone, hours, brief time). | They have approved both files. |
| 10 min | `people.md` and `voice.md` from the user's recent mail, Slack and calendar. | They have approved both files. |
| 10 min | First tasks from the last 14 days of mail and Slack. | 5-10 task files the user chose. |
| 5 min | `brain schedule install`, `brain notify-test`, first brief with `brain brief --detach` (it takes 1–3 minutes). | The user has read their first brief. |
| 5 min | Wrap up: Time Machine, how to ask, `brain doctor`, `brain report`, updates. | The user knows how to reach you. |

Leave the user with: the brief arrives on workdays at their brief time, or soon after they open
the Mac; they always press Send; "something's wrong" in a chat starts the troubleshoot skill.

## Support loop

1. The user runs `brain report` (or asks their assistant to) and emails you the zip.
2. Read `summary.txt`, `doctor.json`, `runs.jsonl` and the log tails. Reproduce the problem here.
3. Fix it with a regression test that fails before the fix.
4. Bump `kit/.kit/VERSION` (patch for fixes, minor for features).
5. `pytest`
6. `python3 tools/build_release.py`
7. `gh release create vX.Y.Z dist/* --title "vX.Y.Z" --notes "<first line for the user>"`. The
   first line of the notes is what the user's brief shows after "Update available": plain
   English, under 80 characters.
8. Within a day their brief offers the update; they say "update". Ask them for a fresh
   `brain report` if you need to confirm.

## Rollback

- **On the user's Mac:** a failed update restores its snapshot by itself. To undo a finished one:
  `brain rollback` (latest snapshot), `brain rollback --list`, `brain rollback --snapshot NAME`.
  The last three snapshots are kept.
- **On your side:** never delete or overwrite a published release. Ship a new patch release that
  reverts the change, so every Mac moves forward to a good version.

## What you can and can't see

- **You can see** only what the user sends you: the `brain report` zip. It holds versions, their
  settings, doctor results, the last 100 run lines and log tails, with emails, home folders, tokens
  and long keys replaced by placeholders.
- **You can't see** their mail, calendar, Drive, Slack, briefs, tasks, notes or keys. Nothing
  leaves their Mac unless they send it, and there is no remote access. (Claude's Remote Control is
  off by default on Team plans; leave it off unless you both want it.)
