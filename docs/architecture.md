# Architecture (build contract)

This is the contract every part of the kit builds against. Keep it current when an interface
changes.

## What the kit is

A template "company brain" that runs on one person's Mac in `~/brain`. The kit contributes
rules, skills and a small Python CLI (`brain`). The person's answers, tasks and history are theirs
and never shipped or overwritten. Claude Code and Codex share the same files. The chat UI is
T3 Code; the CLI and launchd job work without it.

A fresh install is memory only: T3 Code, Claude Code under it (T3 needs it and doesn't bundle
it) and the brain folder. Google (`gws`), Codex, Slack and the daily schedule with its morning
brief are add-ons the person turns on later by asking in a chat (`brain google connect`,
`brain tools install codex`, `brain secret set slack --gui`, `brain schedule install`). A
consultant doing the full setup can ask for the extra tools up front (`BRAIN_EXTRA_TOOLS`, below).

## Repo layout

```text
install.sh                  one-line installer (bash 3.2: macOS default shell tools only)
                            env: BRAIN_EXTRA_TOOLS="gws,codex" -> `brain install --with gws,codex`
kit/                        files that land in ~/brain as KIT-OWNED (replaced on update)
  AGENTS.md  CLAUDE.md
  .claude/settings.json
  .claude/skills/<skill>/SKILL.md
  .codex/…                  Codex project config, if supported (see docs/sources.md)
  docs/                     short user guides + setup-prompts.md (Claude in Chrome prompts)
  tasks/TEMPLATE.md
  .kit/VERSION
  .kit/tools.json           external tools: which are required / standard, how to install (see Tools)
  .kit/slack-manifest.yaml
  .kit/bin/brain            entry point (python3, adds .kit/lib to sys.path)
  .kit/bin/gws              guard shim in front of the real gws (refuses send/write methods)
  .kit/bin/brain-guard      Claude Code PreToolUse hook entry (blocks sends, secret reads, kit edits,
                            destructive commands)
  .kit/codex/brain-starter.rules  Codex forbidden rules, copied to ~/.codex/rules/ on install/update
  .kit/lib/brainkit/        the Python package (stdlib only, Python 3.9+)
templates/                  files created in ~/brain ONLY IF MISSING (user-owned afterwards)
  me.md strategy.md people.md voice.md
  tasks/active/.keep tasks/later/.keep tasks/archive/.keep
  .kit/config.json          the user's settings (timezone, brief time, …)
migrations/NNNN_name.py     def migrate(workspace: Path) -> str (0002_task_format: see Task files)
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
| `.kit/state/session-start.json` | generated | the session-start hook's last check try and last "update available" line |
| `.kit/state/migrations.json`, `.kit/state/migrations/<NNNN>/` | generated | migrations done; copies of files a migration changed (0002: `tasks/…` originals) |
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
| `doctor.py` | `brain doctor [--json] [--quick]`: checks with ok/warn/fail/info + fix hint (see Doctor) |
| `report.py` | `brain report`: redacted diagnostics zip in `reports/` |
| `gws.py` | run gws (JSON), refuse send methods, auth status, draft helper |
| `google_cli.py` | `brain google connect|status`, `brain draft email`; `connect` first installs gws when it's missing |
| `browser.py` | open a link in Chrome when installed (Claude in Chrome lives there), else the default browser |
| `slack_api.py` | Slack Web API client (read-only), injectable transport |
| `slack_sync.py` | cursor-based sync to `ledgers/slack/` (72 h re-read for new thread replies) |
| `slack_cli.py` | `brain slack whoami|sync|recent|search|thread|channels|create-app` |
| `engine.py` | headless engine run (`claude -p …`) with an allowed-tools list |
| `taskfile.py` | the one task-file reader (frontmatter, threads, Outcome, Pinned, Activity), both formats (see Task files) |
| `brief.py` | data packet → engine writes brief → header stamp → Gmail draft (catchup notifies) |
| `brief_fallback.py` | plain brief rendered from the packet when the engine fails |
| `jobs/*.py` | `health`, `slack_sync`, `update_check`, `brief`, `weekly_review` job wrappers |
| `manifest.py` | hash files, load/save `manifest.json` |
| `safe_tar.py` | safe tar extraction (no absolute paths, no `..`, no links outside) |
| `apply.py` | `brain _apply`: install/update a release dir into a workspace |
| `update.py` | `brain update|rollback`, update check against GitHub releases |
| `snapshot.py` | pre-update snapshots and restores (exact modes and links) |
| `tools.py` | `brain tools install [NAME…] [--force]|status`: external tools from `tools.json`; no names = the standard set |
| `mac_apps.py` | find an installed `.app` by name pattern; read an app's `latest-mac.yml` update feed (no YAML library) |
| `install.py` | `brain install [--with TOOLS]`: fresh install from a release dir (used by install.sh); `chmod 700` the workspace |
| `session_start.py` | `brain _session-start` (hidden): Claude Code SessionStart hook, says when a kit update is waiting |
| `repair.py` | `brain repair`: re-link skills for Codex, re-copy Codex rules, fix the `brain` link and PATH block |
| `guard.py` | Claude Code PreToolUse hook (`.kit/bin/brain-guard`): blocks sends, secret reads, kit-file edits |
| `destructive.py` | the hook's rules for destructive commands: recursive `rm`, `sudo`, `pkill`, `killall`, `git clean`, `git reset --hard`, force-pushes |
| `trash.py` | `brain trash <path>...`: move items to `~/.Trash`, never delete (see Trash) |

## Task files

One file per outcome in `tasks/active|later|archive/`, in the owner's shared task format so his
checker, brief and tidy-ups can later run on kit files unchanged. `kit/tasks/TEMPLATE.md` is the
reference copy.

```text
---
title: "…"   priority: "P2"   created_at / updated_at: "YYYY-MM-DD"   due_at: ""
completed_at: ""   links: []           (optional: project, blocker, comms: {…})
---
## Outcome                what done looks like
## Pinned                 settled facts, each with a (YYYY-MM-DD)
## Threads / Open Loops   top-level bullets, see below
## Activity               newest first: ### YYYY-MM-DD HH:MM UTC, then "- …" lines; append only
```

Threads: `- [ai] …` (ready; scheduled work is `- [ai] From YYYY-MM-DD: …`), `- [human] …` (a
decision `Q: …?`, an approval `Approve …?`, or a manual action), `- [external] …` with a
`Follow-Up: YYYY-MM-DD` line, and `- Deferred (on <trigger>; review by YYYY-MM-DD): …`. Indented
field lines: `Context:`, `Options: A :: consequence | B :: consequence`, `Recommend:`, `Source:`,
`Draft:`, `To:`, `Channel:`, `Snooze-Until:`, `Follow-Up:`.

`taskfile.py` is the only parser. `parse_frontmatter(text, defaults=True) -> (meta, body)` and
`extract_threads(text) -> [thread]` return the same shapes as the owner's
`taskstore.parse_frontmatter` / `taskstore.extract_threads` (thread keys `index, tag, text, title,
kind, why, brief, recommend, preview, snooze_until, park_until, defer_hash, start, end`, plus
`to, channel, context, options, source, draft, follow_up, …` when set). Also `task_meta`,
`outcome`, `pinned`, `activity_entries`, `last_activity_date`, `read_task`. Differences from the
owner's reader, to keep in mind when the shared one is swapped in:

- It still reads the kit's old format (0.2.x: `created`/`updated`/`due`, `## Next` tagged
  `[ai]`/`[me]`/`[waiting]`, `## Log` of dated lines, or tagged lines anywhere outside the Log),
  because `brain rollback` and an interrupted update can leave old files beside new code. Old
  tags come back as `human`/`external`; `task_meta` copies old keys to the new names.
- Forgiving of hand edits: byte-order mark, `\r\n`, `*` bullets, upper-case tags, single or double
  quotes; keys lower-cased; lines inside `<!-- -->` are never threads (`start`/`end` count lines
  after the mark and `\r` are dropped).
- No retired `parked` tag and no owner-only `approved` kind.

The brief and weekly-review packets carry, per task: `path, title, priority, due` (from `due_at`),
`outcome, next_steps` (`"[tag] text"`), `threads` (`tag, kind, text` and any set card fields),
`last_log` (newest Activity date). The names `due`, `next_steps` and `last_log` are kept from 0.2
so the skills keep working; the tags in them are the new ones.

Migration `0002_task_format` (0.2.x → 0.3.0) converts every `*.md` under the three folders
(sub-folders too; not hidden files, links or `TEMPLATE.md`): renames `created`/`updated`/`due`
to `*_at` in place (values verbatim; an existing new key wins and the old line stays), adds
`completed_at` (in `archive/`, the `updated` date when it is one) and `links: []`, renames
`## Next`/`## Next steps` → `## Threads / Open Loops` and `## Log`/`## History`/`## Updates` →
`## Activity`, retags `[me]` → `[human]` and `[waiting]` → `[external]` in the Threads section
only, and adds empty `## Pinned` after the Outcome (and any missing Outcome, Threads or Activity).
Old Log lines stay word for word and in order: when the Activity section has no `###` heading
yet, a `### YYYY-MM-DD` heading (date only: the old lines carry no time) goes above each run of
lines starting with the same date, so each becomes an Activity entry the owner's checker reads by
its date; lines before the first dated one stay at the top. Each changed file is first copied
(times kept) to `.kit/state/migrations/0002/<its path>` (an existing different copy is never
overwritten: `name-2.md`), is written atomically and keeps its mode and modified time (the weekly
review reads mtime). A file with no or unclosed frontmatter, a non `key: value` frontmatter line,
non-UTF-8 bytes, or no task sections or tags at all is left untouched and named in the result
line. A second run finds nothing to do.

For the later step: what the owner's checker (`validate.py`) requires that kit files don't have.
Errors: `project` (non-blank) and `comms` keys; no top-level keys beyond its list (so a user's own
key, or an old `created` kept beside `created_at`, is an error); exactly Outcome → [Pinned] →
Threads / Open Loops → Activity as `##` sections (a hand-added `## Notes` is an error);
`completed_at` set on every archived file; `due_at` a valid date; frontmatter starting with exactly
`---\n` (no byte-order mark or `\r\n`) and values in double quotes only (it doesn't strip single
quotes, so `due_at: '2026-11-01'` fails); `links` that aren't web addresses must exist. Warnings: a `Follow-Up:` on
every active `[external]` (migrated `[waiting]` lines have none), `Context:` on every `Q:`/`Approve`
card, at most 3 ready `[ai]` lines, a date on every pin, P1 needs `due_at` or `blocker`, its
`project` list is the owner's own. It reads only the first date of an Activity heading, so the
migration's date-only `### YYYY-MM-DD` passes, though the spec writes `### YYYY-MM-DD HH:MM UTC`.

## Trash (`brain trash`)

The safe way to remove something, since the guards block deleting folders for good. Each path
(relative to the current folder) is moved into `~/.Trash` with `os.rename`; nothing is deleted.
A name already in the Trash becomes `name 2.ext`, `name 3.ext`, … (time-stamped if the Trash
can't be read). Refused, with one `Not moved: …` line each: a missing path, anything outside the
home folder, the home folder, the Trash or anything in it, the brain folder or a folder holding
it, `.kit/`, and kit-owned files (the manifest's `kit_files`, plus `AGENTS.md`, `CLAUDE.md` and
`.claude/settings.json`) or folders holding them. A final link is moved, not its target. Each
move prints `Moved <path> to the Trash.` (with the new name when it changed). Exit 0 when all
moved, 1 if any was refused, 2 off a Mac (nothing moved). Finder's "Put Back" doesn't know
these items. It changes files, so it stays off the allow list.

## Tools (`.kit/tools.json`)

`{"tools": [ … ]}`, one object per tool, in install order:

| Field | Type | Meaning |
|---|---|---|
| `name` | string | `claude`, `t3`, `gws`, `codex` (what `brain tools install NAME` takes) |
| `required` | bool | setup isn't finished without it (only `claude`) |
| `default` | bool, absent = true | in the standard set: a fresh `brain install` and `brain tools install` with no names get it (`claude`, `t3`); `false` = add-on, installed only by name (`gws`, `codex`) |
| `check` | list of strings | the command that proves it runs (`[]` for apps) |
| `install.type` | string | `script`, `archive-binary`, `mac-app-zip`, `mac-app-feed`, `manual` |

Per type: `script` takes `url` (https), `shell` (`bash`|`sh`), optional `args`. `archive-binary`
takes `version`, `assets.<arm64|x86_64>.url` + `sha256` and/or `sha512` (hex), `binary`, `dest`.
`mac-app-zip` takes pinned `assets` like archive-binary plus `app` (exact .app name).
`mac-app-feed` (T3 Code) pins nothing: `feed` (https URL of the app's electron-builder
`latest-mac.yml`), `zip_suffix.<arm64|x86_64>` (`-arm64.zip` / `-x64.zip`) and `app_glob`
(`T3 Code*.app`). The installer reads the feed's `version` and `files` (`url`, base64 `sha512`),
downloads the matching zip from beside the feed (or an https URL it names), refuses a plain name
with `/` or `..`, a non-https address or a sha512 that isn't 64 bytes, verifies the sha512, then
`ditto -x -k` and swaps the app into /Applications (or ~/Applications). Any installed app
matching `app_glob` counts as present and is never replaced (T3 updates itself; `--force`
reinstalls beside it). `brain doctor`'s `t3 app` check uses the same pattern
(`mac_apps.T3_APP_GLOB`). T3's `SHA256SUMS` covers only its CLI tarballs, so the feed is the
integrity source for the desktop zips. A `TODO` url or hash is refused, never guessed.

`brain install` installs the standard set plus `--with` names (repeatable, comma or space
separated; unknown names are skipped with a note). When a required tool still isn't there it
says so plainly and exits 3 (the brain folder stays in place; re-running is safe); install.sh
turns exit 3 into a "paste the same line again" message. Other tool problems are reported and
the install still finishes (exit 0).

## Doctor

Statuses: `ok`, `warn` (worth fixing), `fail` (broken; exit code 1), `info` (nothing to fix).
An add-on that was never set up is `info` with a hint in plain words, so a healthy memory-only
Mac exits 0: `google` without gws or without a saved Google login ("Google isn't connected
(optional)", fix `to add it, say "connect Google" in a chat`), `slack` with no saved token
(`to add it, say "connect Slack" in a chat`), `schedule` not loaded (`to get a morning brief, say
"turn on my morning brief" in a chat`), `codex` not installed. With no schedule there is no
`last catch-up` check. A Google login that was saved and has since expired, been revoked or lost
permissions stays `fail`; an unreadable `gws auth status` is `warn`.

## Claude Code hooks (`kit/.claude/settings.json`)

- `PreToolUse` (Bash|Read|Edit|Write|MultiEdit|NotebookEdit): `${CLAUDE_PROJECT_DIR}/.kit/bin/brain-guard`.
- `SessionStart`, matcher `startup`, command `${CLAUDE_PROJECT_DIR}/.kit/bin/brain`, args
  `["_session-start"]`, `timeout` 10 s. At most once per `update_check_hours` (counting the
  scheduled job's checks and the hook's own tries) it runs the same release check
  as the `update_check` job with a 4 s network timeout, and records an ok answer as an
  `update_check` row (`data.source = "session-start"`); failures go only to
  `.kit/state/session-start.json`, never the runs ledger, and a failed try is repeated after
  an hour. In scheduled (headless) runs, which `engine.py` marks with `BRAIN_ENGINE_RUN=1`, the
  hook does nothing, so a run nobody reads can't use up the chat's notice. When the newest `update_check` row says
  a newer version than the installed one is out, it prints one line, at most once per
  `update_check_hours` per version:
  `A brain kit update is available (v0.2.0 -> v0.2.1: <first line of the notes>). Tell the user in one line and offer to run it; if they say yes, run "brain update --yes".`
  Otherwise, and on any error, it prints nothing; it always exits 0. Claude Code adds a
  SessionStart hook's stdout to the chat's context and waits for it before the first answer,
  hence the short timeouts. `brain update --yes` stays off the allow list.

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
- `update_check`: interval `update_check_hours` (the session-start hook's ok checks count too).
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

0. The scheduled `update_check` job, or the session-start hook in a new chat, notices a release;
   the person says yes and `brain update --yes` runs.
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
