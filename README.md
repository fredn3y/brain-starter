# brain-starter

A template "company brain" for one person's Mac. It turns Claude Code (and, optionally, Codex)
into a work assistant that reads your Gmail, Calendar, Drive and Slack, keeps your task list,
writes you a morning brief, and drafts replies that you send yourself.

It's made for a consultant to set up with a busy, non-technical person in one session of about 90
minutes. Everything runs on the user's Mac, in `~/brain`, and in their own accounts. The chat
window is [T3 Code](https://github.com/pingdotgg/t3code); the `brain` command and the daily
schedule work without it.

## Install

On the user's Mac, in Terminal:

```bash
curl -fsSL https://github.com/fredn3y/brain-starter/releases/latest/download/install.sh | bash
```

Then open a chat in the brain and say "set me up" (the `onboard` skill). The consultant's script
for the session is in [docs/runbook.md](docs/runbook.md).

## What it installs

- **Claude Code**, through Anthropic's native installer (stable channel). Required.
- **gws**, the Google Workspace CLI, at a pinned version checked against its SHA-256. Required.
- **Codex** (OpenAI's installer) and **T3 Code** (the chat app). Optional.
- **`~/brain`**: the kit (rules, skills, the `brain` command) plus the user's starter files.
- **A LaunchAgent** that runs the catch-up at their brief time and hourly, and after the Mac wakes.

## Safety model, honestly

- **Hard limits, enforced by Google and Slack:** Calendar and Drive are read-only scopes; Slack is a
  read-only user token with no bot. The brain can't post to Slack or change events or files.
- **One soft spot:** there is no drafts-only Gmail scope, and `gmail.compose` can technically send.
  Sending is blocked by pattern-based guards (a gws wrapper, a Claude Code hook and deny rules,
  Codex forbidden rules) plus the rules in `AGENTS.md`. They stop mistakes, not a determined
  attacker.
- **Secrets** live in the macOS Keychain. The user types them into a private Mac dialog, never
  into chat.
- **T3 Code starts threads in Full access** by default, so only the deny rules, the hook and the
  Codex rules stand between the agent and the user's shell.
- **Data** stays on the user's Mac and in their accounts; the AI provider processes what the
  assistant reads. The consultant sees only the redacted `brain report` zip the user chooses to
  send.

Details and limits: [docs/security.md](docs/security.md).

## How updates work

- **Kit files** (`AGENTS.md`, `CLAUDE.md`, `docs/`, `tasks/TEMPLATE.md`, `.claude/settings.json`, the
  kit skills, `.kit/`) are replaced on update. **The user's files** (`me.md`, `strategy.md`,
  `people.md`, `voice.md`, `tasks/`, `briefs/`, `.kit/config.json`, `my-*` skills,
  `.claude/settings.local.json`) never are.
- A kit file the user edited is kept, and the new version lands beside it as `<file>.new`.
- Each update snapshots the folder first and restores it if the post-update check fails.
  `brain rollback` restores a snapshot by hand.
- A daily check looks for a new GitHub release. The user's brief then says "Update available",
  and they say "update" in a chat.

## Development

```bash
pytest                          # runs on Linux; macOS commands are faked through a Runner
python3 tools/build_release.py  # dist/brain-starter-vX.Y.Z.tar.gz plus SHA256SUMS
```

- Build contract: [docs/architecture.md](docs/architecture.md)
- Consultant runbook: [docs/runbook.md](docs/runbook.md)
- Mac dry run before a client session: [docs/dry-run-checklist.md](docs/dry-run-checklist.md)
- Facts the build relies on: [docs/sources.md](docs/sources.md)

## License

MIT. See [LICENSE](LICENSE).
