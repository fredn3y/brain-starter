---
name: troubleshoot
description: Use when something isn't working (no brief, an error, a missing connection, no notifications), when the user asks about updates or wants to undo one, or when they need to send a diagnostics report to their consultant.
---

# Help

Fix what you can, explain the rest in plain words, and offer a report when you're stuck.

## Check first

1. Run `brain doctor`. Read every line that isn't ok.
2. Tell the user what's wrong in one plain line each, and the fix. Do the fixes you can run
   yourself; walk them through the ones they must do.
3. Run `brain doctor` again to confirm.

## Commands

| Need | Command |
|---|---|
| full check (`--json` for details) | `brain doctor` |
| is there an update? | `brain update --check` |
| install the update | `brain update --yes` (snapshot first; undoes itself if it fails) |
| undo the last update | `brain rollback` (`--list` shows snapshots; `--snapshot NAME` picks one) |
| file for the consultant | `brain report` (a zip in `reports/`, shown in Finder) |
| is the schedule on? | `brain schedule status` |
| what would run now, and why | `brain catchup --dry-run` |
| make today's brief again | `brain brief --detach`, then after a minute or two show the newest file in `briefs/` |
| test a notification | `brain notify-test` |
| which keys are saved | `brain secret list` |
| missing tools | `brain tools status`, then `brain tools install` |

## Common problems

| Problem | Fix |
|---|---|
| Claude says the login expired or the user isn't logged in | Terminal: `claude`, log in (Claude account with subscription, their Team plan, Authorize), then `/exit`. |
| Google missing, expired or refused | Ask them to run `brain google connect` in Terminal, then run `brain google status`. |
| Google says `org_internal` | The user signed in with a non-work account: connect again with their work account. |
| Google says "Access blocked" with "response_type" | Run `brain google connect` again; it opens the page itself. Don't click links in Terminal. |
| Google refuses access by company policy | Show the "If Google blocks the sign-in" prompt in `docs/setup-prompts.md` for the Workspace admin. |
| Slack token rejected | The user opens https://api.slack.com/apps, picks "Brain (read-only)", OAuth & Permissions (reinstall if offered), and copies the `xoxp-` token themselves. Then `brain secret set slack --gui` and `brain slack whoami`. App gone: redo `brain slack create-app` and prompt C. |
| Slack says a permission (scope) is missing | Re-create the app: `brain slack create-app`, prompt C, then `brain secret set slack --gui`. |
| No notifications | System Settings, Notifications, Script Editor: turn on Allow notifications. Then `brain notify-test`. |
| Brief is late | Normal if the Mac was asleep at brief time: it runs soon after the user opens it. Check `brain catchup --dry-run`. |
| No brief at all | `brain schedule status`; if not loaded, `brain schedule install`. Check System Settings, General, Login Items: the brain's background item must be allowed. |
| Company-managed Mac blocks the background job | Ask IT to allow the brain's background item (`local.brain-starter.catchup`). |
| `<file>.new` after an update | The user had edited a kit file, so the update kept their version and put the new one beside it. See below. |
| Brief fails at night with a login error, but chat works | The user runs `claude setup-token` in Terminal (it shows a long-lived login token), then `brain secret set claude-oauth-token --gui` and pastes it into the private box. Never in chat. |

## `.new` files after an update

1. Show what differs: `diff -u <file> <file>.new`.
2. Usually keep the new one: `mv <file>.new <file>`. Putting the kit's own version back is the one
   change to a kit file you may make. If the user's change still matters, move it into their own
   files or a `my-*` skill first.
3. Tell the user which one you kept.

## Updates

- When the user says "update": run `brain update --yes` and summarise the result in 2-3 lines.
- If it failed, it has already rolled back. Run `brain doctor`, then offer `brain report`.

## Still stuck

Run `brain report`. Tell the user: "I've made a file for your consultant. It's open in Finder:
send it to them by email. It has settings, check results and recent logs with emails and keys
blanked out; none of your mail, tasks or notes."
