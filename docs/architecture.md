# Architecture (build contract)

This is the contract every part of the kit builds against. Keep it current when an interface
changes.

## What the kit is

A template "company brain" that runs on one person's Mac in `~/brain`. The kit contributes
rules, skills and a small Python CLI (`brain`). The person's answers, tasks and history are theirs
and never shipped or overwritten. Claude Code and Codex share the same files. The chat UI is
T3 Code; the CLI and launchd job work without it.

## Repo layout

```text
install.sh                  one-line installer (bash 3.2: macOS default shell tools only)
kit/                        files that land in ~/brain as KIT-OWNED (replaced on update)
  AGENTS.md  CLAUDE.md
  .claude/settings.json
  .claude/skills/<skill>/SKILL.md
  .codex/…                  Codex project config, if supported (see docs/sources.md)
  docs/                     short user guides + setup-prompts.md (Claude in Chrome prompts)
  tasks/TEMPLATE.md
  .kit/VERSION
  .kit/tools.json           pinned external tools (version, per-arch URL, sha256)
  .kit/slack-manifest.yaml
  .kit/bin/brain            entry point (python3, adds .kit/lib to sys.path)
  .kit/bin/gws              guard shim in front of the real gws (refuses send/write methods)
  .kit/bin/brain-guard      Claude Code PreToolUse hook entry (blocks sends, secret reads, kit edits)
  .kit/codex/brain-starter.rules  Codex forbidden rules, copied to ~/.codex/rules/ on install/update
  .kit/lib/brainkit/        the Python package (stdlib only, Python 3.9+)
templates/                  files created in ~/brain ONLY IF MISSING (user-owned afterwards)
  me.md strategy.md people.md voice.md
  tasks/active/.keep tasks/later/.keep tasks/archive/.keep
  .kit/config.json          the user's settings (timezone, brief time, …)
migrations/NNNN_name.py     def migrate(workspace: Path) -> str
tools/build_release.py      builds dist/brain-starter-vX.Y.Z.tar.gz + SHA256SUMS
tests/                      pytest; macOS commands faked through Runner
docs/                       for the consultant: architecture, runbook, security, dry run, sources
```

## Workspace layout (`~/brain`)

| Path | Owner | Notes |
|---|---|---|
| `AGENTS.md`, `CLAUDE.md`, `docs/`, `tasks/TEMPLATE.md` | kit | `CLAUDE.md` is `@AGENTS.md` |
| `.claude/settings.json`, `.claude/skills/<kit skill>/` | kit | |
| `.claude/skills/my-*/`, `.claude/settings.local.json` | user | never touched |
| `.agents/skills/<name>` | generated | symlink → `../../.claude/skills/<name>` for every skill dir, rebuilt on apply |
| `.kit/` (except `config.json`, `snapshots/`, `state/`) | kit | code, manifest, VERSION, tools.json |
| `.kit/config.json` | user | created from template; never overwritten |
| `.kit/manifest.json` | kit | manifest of the installed release (hashes of kit-owned files) |
| `.kit/snapshots/` | generated | pre-update tarballs, keep last 3 |
| `.kit/run.lock` | generated | catch-up / update lock |
| `me.md`, `strategy.md`, `people.md`, `voice.md` | user | filled by `/onboard` |
| `tasks/active|later|archive/*.md` | user | |
| `briefs/YYYY-MM-DD.md`, `briefs/weekly-YYYY-Www.md`, `briefs/.packets/` | generated/user | |
| `ledgers/runs.jsonl` | generated | one line per job run |
| `ledgers/slack/YYYY-MM.jsonl`, `ledgers/slack-cursor.json` | generated | |
| `logs/brain.log`, `logs/launchd.log` | generated | rotated at 1 MB, keep 3 |
| `reports/` | generated | `brain report` bundles |

## Python package `brainkit` (stdlib only, must run on Python 3.9)

No `match`, no `X | Y` at runtime (use `from __future__ import annotations`), no `datetime.UTC`,
no `tomllib`, no `zip(strict=)`, no `dataclass(slots=/kw_only=)`, no `tarfile` `filter=` unless
feature-detected. Each module stays under ~500 lines with one job.

Shared building blocks (in place; extend, don't fork):

- `runner.py`: `Runner.run(argv, input=None, env=None, timeout=None, cwd=None) -> Result` and
  `FakeRunner` for tests. Every external command (security, launchctl, osascript, gws, claude,
  codex, tmutil, profiles, sw_vers, curl) goes through a Runner.
- `clock.py`: `now(tz_name)`, `parse_iso`, `to_iso`, `fmt_local`.
- `paths.py`: `Workspace(root)` with named paths (see table) and `Workspace.default()`
  (`$BRAIN_HOME` or `~/brain`).
- `config.py`: `Config` dataclass + `load(ws)`; defaults below.
- `runs.py`: `RunsLedger(ws)`: `record(...)`, `last(job, status="ok")`, `recent(n)`.
- `log.py`: `get_logger(ws)` → file + stderr, size-rotated.
- `http.py`: `request(runner, method, url, headers, form)`, `get_json`, `download` through the
  system `curl` (CLT Python may not trust the macOS certificate store; secrets go to curl on
  stdin, never argv).
- `jobs/base.py`: `JobContext`, `JobResult`, `JobSpec`.
- `cli.py`: builds argparse from `COMMAND_MODULES`; each module exposes
  `register(subparsers)` and sets `func=run(args) -> int`. Add a module to the list; don't
  hand-edit other modules' parsers.

Module ownership:

| Module | Purpose |
|---|---|
| `schedule.py` | pure due-job computation (slots, intervals, missed days) |
| `catchup.py` | `brain catchup`: lock, run due jobs in order, notify, record |
| `lock.py` | non-blocking `fcntl.flock` on `.kit/run.lock` |
| `notify.py` | macOS notification via osascript (no-op elsewhere); `brain notify-test` |
| `launchagent.py` | `brain schedule install|uninstall|status`: plist + launchctl; `job_env()` = the one PATH/HOME/USER env for jobs, doctor and reports |
| `secrets.py` | `brain secret set|check|delete <name>`: Keychain via `security` |
| `doctor.py` | `brain doctor [--json]`: checks with ok/warn/fail + fix hint |
| `report.py` | `brain report`: redacted diagnostics zip in `reports/` |
| `gws.py` | run gws (JSON), refuse send methods, auth status, draft helper |
| `google_cli.py` | `brain google connect|status`, `brain draft email` |
| `browser.py` | open a link in Chrome when installed (Claude in Chrome lives there), else the default browser |
| `slack_api.py` | Slack Web API client (read-only), injectable transport |
| `slack_sync.py` | cursor-based sync to `ledgers/slack/` (72 h re-read for new thread replies) |
| `slack_cli.py` | `brain slack whoami|sync|recent|search|thread|channels|create-app` |
| `engine.py` | headless engine run (`claude -p …`) with an allowed-tools list |
| `brief.py` | data packet → engine writes brief → header stamp → Gmail draft (catchup notifies) |
| `brief_fallback.py` | plain brief rendered from the packet when the engine fails |
| `jobs/*.py` | `health`, `slack_sync`, `update_check`, `brief`, `weekly_review` job wrappers |
| `manifest.py` | hash files, load/save `manifest.json` |
| `safe_tar.py` | safe tar extraction (no absolute paths, no `..`, no links outside) |
| `apply.py` | `brain _apply`: install/update a release dir into a workspace |
| `update.py` | `brain update|rollback`, update check against GitHub releases |
| `snapshot.py` | pre-update snapshots and restores (exact modes and links) |
| `tools.py` | `brain tools install|status`: pinned external tools from `tools.json` |
| `install.py` | `brain install`: fresh install from a release dir (used by install.sh); `chmod 700` the workspace |
| `repair.py` | `brain repair`: re-link skills for Codex, re-copy Codex rules, fix the `brain` link and PATH block |
| `guard.py` | Claude Code PreToolUse hook (`.kit/bin/brain-guard`): blocks sends, secret reads, kit-file edits |

## Config (`.kit/config.json`, user-owned) defaults

```json
{
  "timezone": "Europe/London",
  "workdays": [1, 2, 3, 4, 5],
  "brief_time": "07:30",
  "work_hours": [8, 18],
  "slack_sync_minutes": 60,
  "health_hours": 6,
  "update_check_hours": 24,
  "weekly_review": true,
  "brief_gmail_draft": true,
  "engine": "claude",
  "brief_model": "sonnet",
  "kit_repo": "fredn3y/brain-starter",
  "lookback_days_max": 14
}
```

Weekdays are ISO (Monday = 1).

## Jobs and the catch-up runner

Order: `health` → `slack_sync` → `update_check` → `brief` → `weekly_review`.

- `health`: interval `health_hours`, and always before a brief.
- `slack_sync`: interval `slack_sync_minutes` inside `work_hours` on workdays, and always before a
  brief. Skipped (status `skipped`) when no Slack token is stored.
- `update_check`: interval `update_check_hours`.
- `brief`: slot `brief_time` on `workdays`. Due when the most recent slot ≤ now is later than the
  last ok brief. Several missed slots → ONE brief covering since the last ok brief
  (capped at `lookback_days_max`).
- `weekly_review`: slot `brief_time` on Monday; same rule.

A failed job records `status: "error"` and the runner continues; failures appear at the top of
the next brief. Runs never overlap (`lock.py`); a second runner exits 0 at once.

## Runs ledger line (`ledgers/runs.jsonl`)

```json
{"job": "brief", "status": "ok|error|skipped", "started_at": "ISO-8601 with offset",
 "finished_at": "…", "summary": "one line", "detail": "error text or null", "data": {}}
```

## LaunchAgent

Label `local.brain-starter.catchup`; `ProgramArguments` =
`/usr/bin/caffeinate -i -s <python3> <ws>/.kit/bin/brain catchup`;
`StartCalendarInterval` = `[{Hour: H, Minute: M}]` (brief time) + `[{Minute: 0}]` (hourly);
`RunAtLoad` true; `WorkingDirectory` = workspace; `EnvironmentVariables.PATH` =
`<ws>/.kit/bin:~/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`
(absolute); logs to `logs/launchd.log`. Installed with `launchctl bootstrap gui/<uid>`.

## Secrets

Keychain generic passwords, service `brain.<name>`, account = `$USER`. Names: `slack`,
`claude-oauth-token` (fallback if a launchd job can't read the Claude login; exported as
`CLAUDE_CODE_OAUTH_TOKEN`), `airwallex-client-id`, `airwallex-key` (later). Values never printed, logged, or passed to an
engine. `brain secret set <name> --gui` uses a hidden-input macOS dialog so onboarding in chat
never sees the value. Google credentials are gws's own (encrypted, key in Keychain).

## Updates (see `apply.py` / `update.py`)

1. The old code checks GitHub, downloads the release tarball, verifies `SHA256SUMS`.
2. It snapshots the workspace (minus `.kit/snapshots`) to `.kit/snapshots/<ts>-<from>-to-<to>.tgz`.
3. It runs the NEW release's `brain _apply --release-dir <dir> --workspace <ws>`.
4. `_apply`: replace kit files whose current hash matches the installed manifest; keep edited
   kit files and write ours beside them as `<file>.new`; delete removed unedited kit files;
   create missing templates only; rebuild `.agents/skills` links; run pending migrations
   (recorded in `.kit/state/migrations.json`); write the new manifest and VERSION.
5. The old code runs `brain doctor`; on failure or non-zero `_apply` it restores the snapshot and
   removes files the new release added.
6. `brain rollback` restores the most recent snapshot by hand.
