# brain-starter

Gives Claude a memory on your Mac. It installs the [T3 Code](https://github.com/pingdotgg/t3code)
chat app, Claude Code (which T3 Code needs and doesn't bundle) and a `~/brain` folder where the
assistant keeps who you are, your goals, the people you work with, how you write and your tasks.
Setup is a 30-minute chat: you type "set me up" and it interviews you.

**Status:** v0.2.0 has had one test on a real Mac (macOS 26, Apple silicon), on a machine that
already had Claude Code and T3 Code. Fresh downloads of those two, the update notice and the
add-ons are not yet tested on a real Mac.

## Install

Needs a Mac on macOS 13 or newer, Apple's Command Line Tools (`xcode-select --install`), and any
paid Claude plan (Pro, Max or Team). In Terminal:

```bash
curl -fsSL https://github.com/fredn3y/brain-starter/releases/latest/download/install.sh | bash
```

Then open a new Terminal window, run `cd ~/brain && claude`, trust the folder, log in with your
Claude account and type `/exit`. Open T3 Code, add the `brain` folder, start a Claude thread and
type "set me up" (the `onboard` skill). The script for a helper running the session is in
[docs/runbook.md](docs/runbook.md).

## What it installs

- **Claude Code**, through Anthropic's native installer (stable channel).
- **T3 Code**, the current release, checked against T3's own release feed.
- **`~/brain`**: the kit (rules, skills, the `brain` command) plus the user's starter files.
- Only on request: **gws**, the Google Workspace CLI, at a pinned version checked against its
  SHA-256 (`brain google connect` fetches it, or `brain tools install gws`), and **Codex**
  (`brain tools install codex`).

Nothing runs in the background until the user turns on the morning brief.

## Add-ons

The user turns these on later by asking in a chat:

- **Google** ("connect Google"): read Gmail, Calendar and Drive; save Gmail drafts. Needs a
  company Google Workspace account and a Workspace admin: the sign-in is an Internal app in the
  company's Google Cloud, so a personal Gmail can't be connected.
- **Slack** ("connect Slack"): read and search Slack. Needs the right to install an app, or an
  admin's approval.
- **Morning brief** ("turn on my morning brief"): a LaunchAgent writes a short brief on workdays,
  and a weekly review on Mondays. Needs Google first.

## Safety model, honestly

- **Always:** the assistant reads and writes its own folder. It has no mail, calendar, Drive or
  Slack access until the user connects them. Send guards (a Claude Code hook and deny rules,
  Codex forbidden rules, a gws wrapper) and the rules in `AGENTS.md` are installed from the start.
  They stop mistakes, not a determined attacker.
- **T3 Code starts threads in Full access** by default, so only the deny rules, the hook and the
  Codex rules stand between the agent and the user's shell.
- **Secrets** live in the macOS Keychain. The user types them into a private Mac dialog, never
  into chat.
- **Once Google is connected:** Calendar and Drive are read-only scopes. There is no drafts-only
  Gmail scope, and `gmail.compose` can technically send, so Gmail sending rests on the pattern
  guards above.
- **Once Slack is connected:** a read-only user token with no bot. The brain can't post.
- **Once the morning brief is on:** the scheduled run has no shell, because it reads untrusted
  mail and Slack text.
- **Data** stays on the user's Mac and in their accounts; the AI provider processes what the
  assistant reads. On a personal Claude plan, Anthropic trains on chats when the user's "Help
  improve our AI models" setting is on. Whoever helps the user sees only the redacted
  `brain report` zip the user chooses to send.

Details and limits: [docs/security.md](docs/security.md).

## How updates work

- **Kit files** (`AGENTS.md`, `CLAUDE.md`, `docs/`, `tasks/TEMPLATE.md`, `.claude/settings.json`, the
  kit skills, `.kit/`) are replaced on update. **The user's files** (`me.md`, `strategy.md`,
  `people.md`, `voice.md`, `tasks/`, `briefs/`, `.kit/config.json`, `my-*` skills,
  `.claude/settings.local.json`) never are.
- A kit file the user edited is kept, and the new version lands beside it as `<file>.new`.
- Each update snapshots the folder first and restores it if the post-update check fails.
  `brain rollback` restores a snapshot by hand.
- When a new GitHub release is out, the assistant says so at the start of a Claude chat and offers
  it; the user says yes. With the morning brief on, the brief says so too.
- T3 Code updates itself: the app offers its own updates.

## Development

```bash
pytest                          # runs on Linux; macOS commands are faked through a Runner
python3 tools/build_release.py  # dist/brain-starter-vX.Y.Z.tar.gz plus SHA256SUMS
```

- Build contract: [docs/architecture.md](docs/architecture.md)
- Setup runbook: [docs/runbook.md](docs/runbook.md)
- Mac dry run before a real session: [docs/dry-run-checklist.md](docs/dry-run-checklist.md)
- Facts the build relies on: [docs/sources.md](docs/sources.md)

## License

MIT. See [LICENSE](LICENSE).
