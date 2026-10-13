# Mac dry run (about 1 hour)

Run this on your own Mac before the first client session and after any big kit change. Every
item marked **(check on the dry run)** is something the research could not confirm from official
sources: write down what you saw and fix the kit's wording or code if it differs.

## Setup

- A new standard macOS user account on your Mac, so the install starts clean.
- Google: a Workspace account in **your own test organization**. Internal apps need an
  organization; a personal Gmail account can't create one. Never use a client's organization.
- Slack: a free test workspace where you can install apps.
- A recipient address you own for the send tests, so the worst case is an email to yourself.
- Note the Mac model, chip and macOS version here: ______

## Gates

### 1. Install on a clean user

1. Terminal: `curl -fsSL https://github.com/fredn3y/brain-starter/releases/latest/download/install.sh | bash`.
   Expect: it finishes, `~/brain` exists, `brain --version` prints the kit version.
   Before the repo is public: build with `python3 tools/build_release.py`, copy `dist/` to the
   Mac, then run `BRAIN_KIT_TARBALL=~/Downloads/dist/brain-starter-v0.1.0.tar.gz
   BRAIN_KIT_SUMS=~/Downloads/dist/SHA256SUMS bash ~/Downloads/dist/install.sh`.
2. Without Command Line Tools, nothing may call `/usr/bin/python3` before checking for them,
   since that pops Apple's install dialog. Note the exact `xcode-select` error and the
   `python3` dialog text (check on the dry run).
3. `xcode-select -p` can print a folder that no longer exists; confirm doctor still catches
   missing tools (check on the dry run).
4. Python version from the Command Line Tools on macOS 27 (expected 3.9.6): ______ (check on the dry run)
5. gws downloaded by the installer runs without a Gatekeeper warning (the arm64 build is ad-hoc
   signed, the Intel build unsigned; curl downloads aren't quarantined) (check on the dry run).
6. Intel Macs or Rosetta: `uname -m` and `sysctl -in sysctl.proc_translated` output, including
   empty output on Intel: ______ (check on the dry run)

### 2. Claude trust and login

1. `cd ~/brain && claude`. Expect a trust prompt for the folder (note its exact wording; doctor's
   fix text names it) (check on the dry run), then login: Claude account with subscription, the
   Team plan, Authorize. Type `/exit`.
2. `claude auth status`: JSON with `"loggedIn": true`, exit 0. Then `claude auth status --text`.
   Also note the `--text` wording when logged out (check on the dry run).
3. Does any Team org setting gate Claude Code itself beyond the seat? (check on the dry run)
4. `claude doctor`: no settings errors for `~/brain/.claude/settings.json` (a settings file that
   fails validation is silently ignored in `-p` runs, which would drop the deny rules).

### 3. Doctor

1. `brain doctor`. Expect no `fail`; `warn` only for parts not set up yet.
2. `tmutil destinationinfo` with no backup disk: exact text: ______ (doctor looks for "No
   destinations configured") (check on the dry run)
3. `profiles status -type enrollment` works without sudo, and its lines read "MDM enrollment:"
   (check on the dry run).

### 4. Google

1. Run setup prompt B in Claude in Chrome against the test organization. Note:
   - first-visit terms or country screen labels (check on the dry run);
   - whether billing is ever asked for (expected: no) (check on the dry run);
   - the API tile names: "Gmail API", "Google Calendar API", "Google Drive API" or without
     "Google" (check on the dry run);
   - whether one enableflow link takes several `apiid` values separated by commas, and whether
     `?project=<ID>` works on the deep links (check on the dry run);
   - the "OAuth client created" box wording and the download button label (expected: Download
     JSON) (check on the dry run);
   - the downloaded filename (expected `client_secret_<id>.apps.googleusercontent.com.json` in
     Downloads) (check on the dry run).
2. Leave "Use this client for an AI-powered agent" unticked. Does ticking it change anything for
   gws calls or admin policies? (check on the dry run)
3. `brain google connect`. Expect: Google's sign-in page opens by itself in the browser (gws only
   prints the URL; Cmd-clicking it can fail with "Access blocked ... response_type", gws issue
   #695) (check on the dry run). Note the consent button wording (Allow or Continue) (check on the
   dry run). The callback page says "Success! You may now close this window."
4. `brain google status`: Gmail, Calendar and Drive connected.
5. Is "Trust internal apps" needed in a default Workspace (API controls unrestricted)? (check on
   the dry run)
6. gws `--scopes` with short names (`drive,gmail`) fails; the kit must pass full scope URLs
   (check on the dry run).

### 5. Slack

1. `brain slack create-app`. Expect: Slack's create-app page with the manifest filled in. Note
   whether it opens in the default browser rather than Chrome, and whether the copied address
   works in Chrome (check on the dry run).
2. Run setup prompt C. Note: the step order (workspace picker before or after the manifest)
   and the "From a manifest" wording (check on the dry run); the manifest is accepted as-is
   (check on the dry run); the install button text, "Install to Workspace" or "Install to <name>"
   (check on the dry run); the token label, "User OAuth Token" (check on the dry run).
3. The token starts with `xoxp-`, not `xoxe.xoxp-` (rotation must be off; Slack's default when the
   setting is omitted is unknown, so the manifest sets it) (check on the dry run).
4. `brain secret set slack --gui`: the private dialog appears when started from a T3 chat; Cancel
   exits non-zero; the value is stored through `security -i` on stdin (check on the dry run).
5. `brain slack whoami` shows the workspace and your name.
6. `security find-generic-password -s brain.nothing` exit code (expected 44) (check on the dry run).

### 6. Notifications

1. `brain notify-test`. If nothing shows: System Settings, Notifications, Script Editor, Allow
   notifications, then again. Note whether `display notification` works from a LaunchAgent on
   macOS 26 and 27 (check on the dry run).

### 7. Schedule, sleep and catch-up

1. `brain schedule install`. Note the "background item added" notice wording, and where the item
   shows in System Settings, General, Login Items (check on the dry run). Bootstrapping twice
   must not fail; note any "Bootstrap failed: 5" text (check on the dry run).
2. `brain schedule status`: Loaded: yes. Does launchd set `USER` and `HOME` for the agent without
   the plist? (check on the dry run)
3. Close the lid at about :50 and open it after the next hour. Expect a new `catchup` line in
   `tail -n 5 ~/brain/ledgers/runs.jsonl` at wake time, and `brain catchup --dry-run` shows nothing
   due.

### 8. Keychain from launchd with the screen locked

1. Set `brief_time` in `~/brain/.kit/config.json` to three minutes from now (on a weekend, also add
   today to `workdays`, Monday = 1); `brain schedule install`.
2. Lock the screen (Control-Command-Q) and wait five minutes. Unlock.
3. Expect a `brief` line with `"status": "ok"` in `ledgers/runs.jsonl`. If it failed with a login
   or Keychain error: `claude setup-token`, `brain secret set claude-oauth-token --gui`, repeat
   (check on the dry run). Same for gws reading its key from the Keychain.
4. Put `brief_time` back and run `brain schedule install` again.

### 9. The scheduled brief

1. `brain catchup --job brief`. Expect `briefs/YYYY-MM-DD.md` in the format of the
   `morning-brief` skill, a notification, and a Gmail draft to yourself.
2. The run used only the CLI-granted tools (Read, Glob, Grep and writing the brief): nothing else
   changed in `~/brain` (`ls -lt ~/brain | head`). The engine log
   (`~/brain/logs/engine/morning-brief-<date>.log`) shows no Bash tool use: the run is started
   with `--tools Read,Glob,Grep,Edit,Write`.
3. From a T3 chat: "show me today's brief" after deleting today's file. Expect the AI to run
   `brain brief --detach`, return at once, and show the new file a few minutes later.
4. The `/morning-brief` skill expands in `claude -p` (check on the dry run), and the prompt is not
   swallowed by the variadic `--tools`/`--allowedTools` (check on the dry run).
5. Gmail links in the brief open the right message, including the
   `https://mail.google.com/mail/?authuser=<email>#all/<id>` fallback (check on the dry run).

### 10. Every guard blocks a send

Use your own address. Each must fail without sending anything.

1. gws wrapper: `~/brain/.kit/bin/gws gmail +send --to you@example.com --subject t --body t; echo $?`
   Expect exit 3 and "Blocked by your brain's safety rules".
   Also with options between the words:
   `~/brain/.kit/bin/gws gmail users --format json messages send --params '{}'; echo $?` → exit 3.
2. Hook: `echo '{"tool_name":"Bash","tool_input":{"command":"gws gmail +send --to x"}}' | CLAUDE_PROJECT_DIR=~/brain ~/brain/.kit/bin/brain-guard; echo $?`
   Expect exit 2 and the block message. Repeat with the command
   `~/.local/bin/gws gmail users --format json messages send` (a full path past the wrapper) and
   with `security -q find-generic-password -s brain.slack -w`: both exit 2.
3. Deny rules alone (the hook ignores this one): in a T3 Claude thread in Full access, ask it to
   run exactly `gws calendar +send`. Expect a permission-rule denial, not a gws error (check on
   the dry run).
4. Claude end to end: in the same thread, ask it to run
   `gws gmail +send --to you@example.com --subject t --body t`. Expect a block and an offer to draft.
5. Codex rule: `codex execpolicy check --pretty --rules ~/.codex/rules/brain-starter.rules -- gws gmail +send --to x`
   Expect `"decision": "forbidden"`; the same for `gws gmail users --format json messages send`.
6. Codex end to end in T3 Full access (`never` + `danger-full-access`): ask a Codex thread to run
   the same send. Expect the rule to refuse it (check on the dry run).

### 11. Update and rollback

1. Publish test releases v0.1.0 and v0.1.1 to a test repo and point `kit_repo` in
   `~/brain/.kit/config.json` at it. Install v0.1.0.
2. Snapshot the user's files: `cd ~/brain && shasum -a 256 me.md strategy.md people.md voice.md tasks/*/*.md .kit/config.json > /tmp/before.txt`
3. `brain update --check` shows v0.1.1; `brain update --yes` installs it.
4. `shasum -a 256 -c /tmp/before.txt`: every line OK. `cat ~/brain/.kit/VERSION` shows 0.1.1.
5. Edit a kit file by hand first in a second run: expect `<file>.new` beside it after the update.
6. `brain rollback --list`, then `brain rollback`: VERSION is back to 0.1.0 and the user's files still pass.

### 12. T3 Code

1. T3 is signed and notarized: `spctl -a -vv "/Applications/T3 Code (Alpha).app"` and
   `xcrun stapler validate "/Applications/T3 Code (Alpha).app"` (check on the dry run).
2. Add `~/brain` as a project. In a Claude thread, `$` lists the kit skills; ask "what are your
   hard rules?" and expect the `AGENTS.md` rules.
3. In a Codex thread, `$` lists the same skills (through the per-skill links in
   `.agents/skills/`), and Codex follows `AGENTS.md` (T3 has no AGENTS.md logic of its own; this
   relies on Codex's own discovery) (check on the dry run).
4. Optional, not used by the kit: T3 with `disableBypassPermissionsMode` set (check on the dry
   run); pre-setting T3's default by editing `~/.t3/userdata/settings.json` (`defaultRuntimeMode`)
   (check on the dry run); a whole-folder `.agents/skills` link instead of per-skill links (check
   on the dry run).
5. "Auto-accept edits" in T3: does Codex lose network access for gws, as expected? (check on the
   dry run)

### 13. Admin and data checks

1. Claude admin: the Remote Control toggle at claude.ai/admin-settings/claude-code; is it greyed
   out unless "Code on the Web" is on? (check on the dry run)
2. ChatGPT data controls on a personal plan: the "Improve the model for everyone" setting and its
   place under Settings > Data Controls; the Business-plan training default. These came from
   search results, not a fetched page (check on the dry run).
3. Airwallex setup prompt D: every screen name in it is unresearched; walk it once before any
   client uses it (check on the dry run).

### 14. Clean up

`brain schedule uninstall`, remove the Slack test app, delete the test Google client, and delete
the test macOS user.
