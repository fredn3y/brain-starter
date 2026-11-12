# Security

What stops the brain from doing harm, how strong each guard is, and where the data goes.

**Hard** guards are enforced outside the AI (Google, Slack, Anthropic). **Soft** guards are text
patterns or instructions on the user's Mac: they stop honest mistakes and most prompt-injection
attempts, but a determined agent could get around them.

A new brain is memory only: no Google login, no Slack token and no schedule exist until the user
asks for them. The Google and Slack rows below apply only once those are connected. The soft
guards against sending are installed from the start either way.

## Guards

| Guard | Kind | What it stops | Limits |
|---|---|---|---|
| Google scopes (once connected): `calendar.readonly`, `drive.readonly`, `gmail.readonly`, `gmail.compose` | hard | Changing or deleting events and Drive files; archiving, labelling or deleting mail. | `gmail.compose` is "Manage drafts and send emails": there is no drafts-only Gmail scope, so Gmail sending rests on the soft guards below. |
| Internal Google app (once connected) | hard | Anyone outside the user's Workspace organization using the client (`org_internal` error). | Anyone inside the user's organization could use the client if they had the file. |
| Slack user token with read-only scopes, no bot (once connected) | hard | Posting, editing, reacting, inviting. | It reads whatever the user can read in their channels and DMs, and searches the workspace. |
| No computer-use tool installed by the kit | kit choice | The agent driving apps on the user's Mac. | Claude in Chrome is used only to set up the Google and Slack add-ons, with the user watching. The kit can't stop the user adding other Claude features themselves. |
| Secrets in the macOS Keychain, typed into a hidden dialog | hard-ish | Tokens in chat, files, logs or the AI's context. | Any process running as the user could ask the Keychain. |
| gws wrapper (`.kit/bin/gws`) | soft | `+send`, `+reply`, `+reply-all`, `+forward` after `gmail`; the word `send` as a command word after `gmail` wherever options sit (`users --format json messages send`); `auth … export`. | Only when `gws` resolves to the wrapper; the real binary by full path skips it (the hook catches that in Claude). |
| Claude Code PreToolUse hook (`brain-guard`) | soft | Sends (full paths, options between words, line continuations, `sh -c`, `eval`, `$(…)`, subshells, `xargs`), Slack posting endpoints, Keychain reads (`security` with any options, `security -i`), credential files, `brain _apply`, edits to kit files; destructive commands (see the next row). | Tokenises the command text: encoded commands (base64 into a shell) or another language's HTTP client can slip past. It allows the call if the hook itself crashes. |
| Destructive commands (hook, deny rules, Codex rules) | soft | `rm` whenever it is recursive (`-r`, `-R`, `--recursive`, alone or with other letters, e.g. `-rf`, `-fr`, `-r -f`, `/bin/rm`); `sudo`, `pkill`, `killall`; `git clean`, `git reset --hard`, `git push` with `--force`, `-f`, `--force-with-lease` (the hook also catches `+branch`). The hook sees these anywhere in a command, as for sends. Block messages for deletes point to `brain trash`, which only moves items into `~/.Trash`. | Text matching only: another language's file-delete call (`python3 -c "import shutil; …"`), `find … -delete`, `rsync --delete`, an encoded command or a git alias still get through. Deny rules and Codex rules catch only the common spellings in fixed places (`rm -vrf …`, `rm dir -r` or `sh -c '…'` pass the deny rules; `git push origin main --force` and `--force-with-lease=…` pass the Codex rules); the hook catches those. `brain trash` itself refuses the home folder, the brain folder, anything outside home and kit files. |
| Claude Code deny rules (`.claude/settings.json`) | soft | The same sends (incl. `gws * send *`), `gws auth export`, Keychain reads (incl. `security -i`), reading credential files, `brain _apply`, the common spellings of the destructive commands above. Deny rules block in every mode, including bypass. The allow list grants only read-only `brain` commands. | They match the command as written: `/full/path/gws …` or `sh -c '…'` slip past (per Anthropic's docs). A settings file that fails validation is silently ignored in `-p` runs. |
| Codex forbidden rules (`~/.codex/rules/brain-starter.rules`) | soft | The same sends, `gws auth export`, Keychain reads (incl. `security -q …`, `security -i`), `brain _apply`, the common spellings of the destructive commands above; gws options placed between the command words are refused outright. | Exact word positions: a full path or an unlisted option doesn't match. They load for every project on the Mac, so `sudo`, `rm -rf` and force-pushes are refused in other Codex projects too. Enforcement in T3's Full access is read from source, not yet seen live. |
| `AGENTS.md` rules and skills | soft | Everything above, as policy: drafts only, no secrets in chat, read only, nothing deleted for good (`brain trash` instead), no `sudo`, no stopping other programs. | Instructions, not enforcement. |

## Known gaps

- **T3 Code defaults to Full access** for new threads: Claude runs in `bypassPermissions`, Codex
  with `approval_policy=never` and `danger-full-access`. Only deny rules, the hook and Codex
  forbidden rules apply there. The default can be changed in T3 under Settings > General > New
  threads > Permissions, but "Auto-accept edits" puts Codex in `workspace-write`, which turns
  network access off, so Codex's Google calls would fail. Test before changing it.
- **The scheduled brief has no shell** (once the morning brief is on). It runs headless Claude with `--tools Read,Glob,Grep,Edit,Write`
  and may only write under `briefs/`, because it reads untrusted mail and Slack text. A prompt
  injected into an email can't run commands from the unattended run.
- **Encoded commands** (base64 into a shell, another language's HTTP client) get past every
  text-matching guard. Only the hard scopes hold against them.
- **Deletes have no hard guard.** On the Mac itself, a delete written in another language or an
  encoded command can still remove files for good. Backups (Time Machine) are the only cover.
- **`disableBypassPermissionsMode` is not set.** It would make Claude refuse bypass mode; how T3
  behaves then is untested.
- **The trust prompt matters.** Until the user accepts Claude's trust dialog for `~/brain`, the
  project's allow rules are ignored (deny rules and the hook still apply).
- **T3 Code is not pinned.** The installer takes T3's current release and checks the download
  against the sha512 in T3's own release feed (`latest-mac.yml`). So the kit trusts T3's release
  feed at install time, the same trust T3's own updater relies on afterwards; the kit adds no
  check of its own on top.
- **The update notice.** At the start of a Claude chat, a SessionStart hook can tell the assistant
  that a kit update exists; its plain-text output is added to Claude's context. The assistant
  offers the update and runs `brain update --yes` only after the user agrees. That command is not
  on the allow list, so outside Full access Claude asks permission first. Codex chats don't get the
  notice.

## Where the data lives

- The user's notes, tasks, briefs, ledgers and logs stay in `~/brain` on their Mac.
- Once connected, Google and Slack data stays in those services; the brain reads it on demand
  and keeps a local Slack history in `ledgers/slack/`.
- What the assistant reads is sent to the AI provider (Anthropic for Claude, OpenAI for Codex) to
  process.
- Keys: Google's login is stored by gws (encrypted, key in the Keychain); the Slack token and any
  other key are Keychain items named `brain.<name>`.
- Whoever helps the user gets nothing unless the user sends a `brain report` zip (redacted; no
  mail, tasks, briefs or notes).

## How the AI providers handle it

- **Claude (personal plans: Pro and Max):** Anthropic trains on the user's chats, Claude Code
  included, when their "Help improve our AI models" setting is on. Check it together at setup
  (claude.ai, Settings, Privacy) and let the user choose.
- **Claude (Team plan):** Anthropic says "By default, we will not use your inputs or outputs from
  our commercial products" for training. The exception is feedback the user sends (thumbs up or
  down, bug reports). Team Owners can switch off the feedback button for members.
- **ChatGPT for Codex (only if installed):** OpenAI says it doesn't train on ChatGPT Business, Enterprise or Edu
  content by default, and that using Codex doesn't change this. On personal plans (Free, Plus,
  Pro) training is on by default unless the user turns off "Improve the model for everyone", which
  also covers Codex. Prefer a Business seat for work use.

Sources and dates: [sources.md](sources.md).
