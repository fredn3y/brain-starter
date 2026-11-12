# Setup runbook

How to set up a brain with someone in one session (in person or over a video call), support them
afterwards, and ship fixes. Run [dry-run-checklist.md](dry-run-checklist.md) on your own Mac
before the first real session and after any big kit change.

The default is memory only: T3 Code, Claude Code and the `~/brain` folder. Google, Slack and the
morning brief are add-ons, covered at the end.

## Before the session

Check these a few days ahead. Each "no" is a blocker to solve first.

| Check | Why |
|---|---|
| A Mac on macOS 13 or newer. Note the model and chip. | Claude Code's native installer needs macOS 13+. macOS 27 runs on Apple silicon only; macOS 26 is the last release for Intel Macs. |
| Apple's Command Line Tools installed beforehand (`xcode-select -p` prints a path; if not, `xcode-select --install`) | The `brain` command runs on Apple's Python (3.9). The download takes 5 to 30 minutes and may ask for the Mac password, so don't leave it for the session. |
| Any paid Claude plan: Pro, Max or Team | Claude Code is included with each. On a Team plan, a seat without it shows "You haven't been added to your organization yet" and the plan's Owner must fix it (setup prompt A). |
| On a personal plan (Pro or Max): look together at the "Help improve our AI models" setting at claude.ai, under Settings, Privacy | Anthropic trains on personal-plan chats, Claude Code included, when it's on. It doesn't on Team plans. The user decides; make sure they know. |
| Company-managed Mac (MDM)? | IT may block installers or Keychain prompts. |
| At least 2 GB free disk | Tools, snapshots and logs. |
| Time Machine is on, or the user is willing to turn it on | The brain folder holds their notes and tasks. |

## The session (about 30 to 40 minutes)

| Time | Step | Done when |
|---|---|---|
| 2 min | Explain it: it remembers who they are, their goals, the people they work with, how they write and their tasks. It drafts; it never sends. Nothing else is connected unless they ask later. | They can say what it remembers and that they always press Send. |
| 5 min | Terminal: run the install line from the README. It fetches only Claude Code and T3 Code. | The installer finishes and prints its next steps. |
| 3 min | Open a new Terminal window. `cd ~/brain && claude`: they trust the folder, log in with their Claude account (Claude account with subscription, Authorize), type `/exit`. | `claude auth status --text` shows their login. |
| 2 min | Open T3 Code. Add the `brain` folder as a project, start a Claude thread, type "set me up". | The `onboard` skill starts with a health check. |
| 10 min | Interview: fills `me.md`, `strategy.md` and their timezone, days and hours. | They have approved both files. |
| 5 min | People: they name the people they work with. | They have approved `people.md`. |
| 5 min | Voice: they paste two or three things they've written, or skip. | They have approved `voice.md`, or skipped it. |
| 5 min | First tasks: what's on their plate. | Task files they chose. |
| 2 min | Wrap up: how to ask, `brain doctor`, `brain report`, updates, extras. | `brain doctor` shows no problems and they know how to reach you. |

Leave the user with:
- their brain remembers them; they tell it when something changes;
- they always press Send;
- at the start of a Claude chat it offers updates; they say yes;
- "something's wrong" in a chat starts the troubleshoot skill; `brain report` makes a file for you;
- Google, Slack and a morning brief are there if they ask.

## Add-on session: the company brain (about 45 minutes more)

For someone who wants their mail, calendar, Drive and Slack read, and a morning brief. Check
these first, as well as the list above:

| Check | Why |
|---|---|
| The user is a Google Workspace admin, or one is on call, and signs in with a company Google account | The Google sign-in is an Internal app in their company's Google Cloud organization. A personal Gmail account can't do this. |
| Claude in Chrome works in the user's Chrome | Setup uses it for Google Cloud and Slack. It's on by default for Team (Organization settings > Claude in Chrome). |
| The user can install apps in Slack, or an admin can approve one | The Slack app is internal, user-token only, read-only. |
| Company-managed Mac: IT allows the brain's background item (`local.brain-starter.catchup`) | The morning brief runs from it. |
| ChatGPT plan, if the user will use Codex | Business plans and personal plans handle data differently (see [security.md](security.md)). Codex is optional: `brain tools install codex`, then `codex login`. |

| Time | Step | Done when |
|---|---|---|
| 15 min | "Connect Google": setup prompt B in Claude in Chrome, then `brain google connect` in Terminal (it fetches the Google tool if needed), then `brain google status`. | Gmail, Calendar and Drive show as connected. |
| 10 min | "Connect Slack": `brain slack create-app`, setup prompt C, `brain secret set slack --gui`, `brain slack whoami`. | `whoami` shows the user's workspace and name. |
| 15 min | Refresh `people.md`, `voice.md` and tasks from recent mail, Slack and calendar. | They have approved the additions. |
| 5 min | "Turn on my morning brief": brief time, `brain schedule install`, `brain notify-test`, first brief with `brain brief --detach` (it takes 1 to 3 minutes). | The user has read their first brief. |

Leave them with: the brief arrives on workdays at their brief time, or soon after they open the
Mac; it says when an update is out.

## Support loop

1. The user runs `brain report` (or asks their assistant to) and emails you the zip.
2. Read `summary.txt`, `doctor.json`, `runs.jsonl` and the log tails. Reproduce the problem here.
3. Fix it with a regression test that fails before the fix.
4. Bump `kit/.kit/VERSION` (patch for fixes, minor for features).
5. `pytest`
6. `python3 tools/build_release.py`
7. `gh release create vX.Y.Z dist/* --title "vX.Y.Z" --notes "<first line for the user>"`. Write
   the first line of the notes for the user: plain English, under 80 characters. The assistant
   sees it with the update notice at the start of a Claude chat, and the brief shows it after
   "Update available".
8. Their assistant offers the update at the start of a Claude chat (and in the brief, if it's
   on); they say yes. Ask them for a fresh `brain report` if you need to confirm.

## When the user is stuck

Work down this list. Go to the next step only when the one before hasn't solved it.

1. Their own assistant: "something's wrong" in a chat runs the troubleshoot skill and `brain doctor`.
2. A report: `brain report`, emailed to you (the support loop above).
3. A call with their screen shared. They type, you guide.
4. Last resort, a remote link, only when they are completely stuck and the steps above have
   failed. It lets an agent on your machine start threads on their Mac through T3 Code's
   linked environments. Agree it with them first, and set it up for that one problem:
   - On their Mac: **Settings → Labs**, turn on Linked environments. **Settings → Sharing &
     access**: turn on T3 Connect (or share the Mac with you over Tailscale), then create a
     pairing link and send you its address and code. The code works once: treat it like a
     password.
   - On yours: link with the least access that does the job (`approval-required` unless there
     is a reason for more; never `full-access`, which also opens their own threads, projects
     and settings).
   - When the problem is fixed, they revoke the link in **Settings → Sharing & access**. A
     link left alone ends after 30 days.

   Linked environments is a T3 Code beta, and this route has not been tried between two
   people's machines yet (see the dry-run checklist).

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
- **You can't see** their brain files, mail, calendar, Drive, Slack, briefs, tasks, notes or keys.
  Nothing leaves their Mac unless they send it, and there is no remote access unless you both
  set up a link as a last resort (see "When the user is stuck"). While a link exists you can
  see the threads it started and what they report back. (Claude's Remote Control is off by
  default on Team plans; on any plan, leave it off unless you both want it.)
