# Sources

The facts the kit relies on, grouped, with where they come from. Checked 2026-10-13 unless a
row or heading gives another date. Anything still unproven is listed in [dry-run-checklist.md](dry-run-checklist.md) as
"(check on the dry run)".

## Claude Code (2.1.296 latest, 2.1.287 stable)

| Fact | Source |
|---|---|
| `-p` skips the trust dialog; settings files that fail validation are silently ignored in `-p`. | `claude --help` (2.1.296); https://code.claude.com/docs/en/cli-reference |
| In an untrusted folder, project `permissions.allow` rules aren't used; deny and ask rules still apply. Trust is accepted by running `claude` there once. | https://code.claude.com/docs/en/permissions |
| Deny rules block in every mode, including `bypassPermissions`. An allow rule can't override a deny rule. | https://code.claude.com/docs/en/permission-modes ; https://code.claude.com/docs/en/permissions |
| Bash rule wildcards; a rule with two wildcards doesn't match the bare command; `/full/path` and `sh -c` get past text rules. `Read(~/path)` anchors at home. | https://code.claude.com/docs/en/permissions |
| Hooks: `args` makes a command hook run directly, without a shell; `${CLAUDE_PROJECT_DIR}` is substituted; exit 2 blocks a PreToolUse call and stderr is the reason; other non-zero exits don't block. | https://code.claude.com/docs/en/hooks |
| A SessionStart hook's plain-text output is added to Claude's context (checked 2026-11-12). | https://code.claude.com/docs/en/hooks |
| The kit's `.claude/settings.json` validates against the published settings schema (0 errors). | https://json.schemastore.org/claude-code-settings.json |
| Project skills live in `.claude/skills/<name>/SKILL.md`; `/name` works in `-p`; Claude never reads `.agents/`. | https://code.claude.com/docs/en/skills ; https://code.claude.com/docs/en/headless ; https://code.claude.com/docs/en/memory |
| A project skill named like a built-in command replaces it in a terminal session; `/help` and `/tasks` are built-ins. | https://code.claude.com/docs/en/skills ; https://code.claude.com/docs/en/commands |
| `CLAUDE.md` containing `@AGENTS.md` is the robust way to share rules. | https://code.claude.com/docs/en/memory |
| `claude auth status` prints JSON and exits 0 when logged in, 1 when not. | https://code.claude.com/docs/en/cli-reference |
| `claude setup-token` makes a one-year token for `CLAUDE_CODE_OAUTH_TOKEN` (model requests only). | https://code.claude.com/docs/en/authentication |
| Native installer puts the launcher at `~/.local/bin/claude`; needs macOS 13+ (rechecked 2026-11-12). | https://code.claude.com/docs/en/setup |
| Claude Code is included with Pro, Max, Ultra and every Team seat (checked 2026-11-12). | https://support.claude.com/en/articles/11145838 |
| On personal plans, Anthropic trains on chats, Claude Code included, when the user's "Help improve our AI models" setting is on (checked 2026-11-12). | https://code.claude.com/docs/en/data-usage |

## Codex (0.162.1)

| Fact | Source |
|---|---|
| `.rules` files in `~/.codex/rules/` load for every project; `prefix_rule(...)` in Starlark; `forbidden` beats `prompt` and `allow`; a pattern element can be a list of alternatives. | https://learn.chatgpt.com/docs/agent-configuration/rules |
| The kit's rules file gives `forbidden` for every send and Keychain read, and no match for reads or draft creation. | `codex execpolicy check` with codex-cli 0.162.1 |
| `AGENTS.md` is read from the project root down to the working folder; skills are read from `.agents/skills` and symlinked skill folders are followed. | https://learn.chatgpt.com/docs/agent-configuration/agents-md ; https://learn.chatgpt.com/docs/build-skills |
| `workspace-write` keeps network access off unless enabled. | https://learn.chatgpt.com/docs/agent-approvals-security |
| `codex login status` exits 0 when logged in, 1 when not. | https://learn.chatgpt.com/docs/auth ; `codex-rs/cli/src/login.rs` |

## T3 Code (v0.1.9 latest stable, 2026-11-11)

The first four rows were checked 2026-11-12; the rest were read at v0.0.45 on 2026-10-13.

| Fact | Source |
|---|---|
| Latest stable is v0.1.9, released 2026-11-11. The kit installs whatever release is latest, not a pinned one. | https://github.com/pingdotgg/t3code/releases/latest |
| The Mac app is `T3 Code (Beta).app` since v0.1.0 (before that, `T3 Code (Alpha).app`); Homebrew cask `t3-code`. | https://github.com/pingdotgg/t3code/releases/latest ; https://formulae.brew.sh/api/cask/t3-code.json |
| The desktop update feed is `latest-mac.yml`, with a sha512 for each file. | https://github.com/pingdotgg/t3code/releases/latest |
| The app offers its own updates, with a button. | https://github.com/pingdotgg/t3code/releases/latest |
| At v0.0.45 the app was signed with a Developer ID (T3 Tools, Inc.). | https://github.com/pingdotgg/t3code/releases/tag/v0.0.45 |
| New threads default to Full access (Settings > General > New threads > Permissions): Claude `bypassPermissions`, Codex `never` + `danger-full-access`. | t3code `docs/user/permission-modes.md`; `ClaudeAdapter.ts`; `CodexSessionRuntime.ts` |
| Composer: `/` for commands, `$` for skills; Claude skills come from the project's `.claude/skills`. | t3code `docs/user/composer.md`; `docs/user/providers-claude.md` |
| Claude Code isn't bundled; T3 finds CLIs on the login shell's PATH. | t3code `docs/user/install.md`; `pnpm-workspace.yaml` |

## Google

| Fact | Source |
|---|---|
| New project; its Location must be the Workspace organization for an Internal app. | https://developers.google.com/workspace/guides/create-project ; https://support.google.com/cloud/answer/15544987 |
| Enable-API deep links for Gmail, Calendar and Drive. | https://developers.google.com/workspace/guides/enable-apis |
| Google Auth platform: Branding, Get Started, App name, User support email, Audience Internal, Contact Information, the User Data Policy agreement, Create. Internal apps list no scopes. | https://developers.google.com/workspace/guides/configure-oauth-consent |
| Clients, Create Client, Desktop app, Name, Create. | https://developers.google.com/workspace/guides/create-credentials |
| The client secret can be viewed and downloaded only once; the "Use this client for an AI-powered agent" checkbox; inactive clients are deleted after six months. | https://support.google.com/cloud/answer/6158849 ; https://support.google.com/cloud/answer/15549257 |
| `org_internal` error for users outside the organization. | https://support.google.com/cloud/answer/15544987 |
| Admin console: Security > Access and data control > API controls > Settings > Trust internal apps; up to 24 hours. | https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data |
| Scope wording: `gmail.compose` "Manage drafts and send emails"; `calendar.readonly`; `drive.readonly`. | https://developers.google.com/workspace/gmail/api/auth/scopes ; https://developers.google.com/workspace/calendar/api/auth ; https://developers.google.com/workspace/drive/api/guides/api-specific-auth |
| Internal apps need no Google verification, even for restricted scopes. | https://developers.google.com/identity/protocols/oauth2/production-readiness/overview |

## gws (0.22.5)

| Fact | Source |
|---|---|
| Release v0.22.5 assets and SHA-256; the OAuth client file is `~/.config/gws/client_secret.json`; `--scopes` needs full scope URLs; login prints the URL rather than opening a browser. | https://github.com/googleworkspace/cli (README, `src/auth_commands.rs`, `src/oauth_config.rs`) |
| Open Mac issues: #695 (Cmd-clicked login URL fails), #886 (login deleted when decryption fails), #729 (Drive 403 for non-members of the project). | https://github.com/googleworkspace/cli/issues |
| `--output` must be inside the current folder, else a validation error. | gws 0.22.5, run locally |
| `+triage --format json` returns `messages[]` with `id`, `from`, `subject`, `date`; `+read --format json` returns `thread_id`, `from`, `to`, `cc`, `subject`, `date`, `body_text`. | gws v0.22.5 source: `helpers/gmail/triage.rs`, `helpers/gmail/mod.rs` |
| `calendar +agenda --dry-run` still calls the API. | gws 0.22.5, run locally |

## Slack

| Fact | Source |
|---|---|
| Manifest: `display_information.name` (max 35 characters) required; `description` max 140; no bot user needed; omit `long_description`. | https://docs.slack.dev/reference/app-manifest |
| A create link with `?new_app=1&manifest_yaml=<encoded>` pre-fills the manifest; then workspace, Next, Create. | https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests |
| OAuth & Permissions, Install to Workspace, Allow, token under OAuth Tokens. | https://docs.slack.dev/app-management/quickstart-app-settings |
| User tokens start with `xoxp-`; token rotation can't be turned off once on. | https://docs.slack.dev/authentication/tokens ; https://docs.slack.dev/authentication/using-token-rotation |
| The ten scopes all support user tokens; `search:read` is user-only. | https://docs.slack.dev/reference/scopes |
| Internal apps keep Tier 3 limits on `conversations.history` and `conversations.replies`. | https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps |

## Claude admin and Claude in Chrome

| Fact | Source |
|---|---|
| Claude Code is included with every Team plan seat (and with Pro and Max: see Claude Code above). | https://support.claude.com/en/articles/11845131-use-claude-code-with-your-team-or-enterprise-plan |
| "You haven't been added to your organization yet" means the seat lacks Claude Code. | https://code.claude.com/docs/en/admin-setup |
| Remote Control is off by default on Team; an Owner turns it on at claude.ai/admin-settings/claude-code. | https://code.claude.com/docs/en/remote-control |
| Claude in Chrome is on by default for Team; it won't enter sensitive data and asks before downloads, authorizations and financial sites. | https://support.claude.com/en/articles/13065128-claude-in-chrome-admin-controls ; https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely ; https://support.claude.com/en/articles/12902446-claude-in-chrome-permissions-guide |

## macOS

| Fact | Source |
|---|---|
| macOS 27 supports Apple silicon only; macOS 26 is the last for Intel. | https://www.macrumors.com/2026/09/10/macos-27-golden-gate-release-date/ |
| `tmutil destinationinfo` needs no root; `latestbackup` does. | https://keith.github.io/xcode-man-pages/tmutil.8.html |
| Command Line Tools Python is 3.9.6; `/usr/bin/python3` is a shim that offers to install them. | https://developer.apple.com/forums/thread/838682 ; https://docs.python.org/3/using/mac.html |
| `display notification` is attributed to Script Editor, whose notifications must be allowed. | https://forum.latenightsw.com/t/trying-to-use-terminal-for-display-notification/5068 |
| `security add-generic-password ... -w` at the end prompts for the value. | https://keith.github.io/xcode-man-pages/security.1.html |
| launchd runs a missed `StartCalendarInterval` job once on wake; use `bootstrap`/`bootout`. | https://keith.github.io/xcode-man-pages/launchd.plist.5.html ; https://keith.github.io/xcode-man-pages/launchctl.1.html |
| Background items appear in System Settings > General > Login Items. | https://support.apple.com/guide/mac-help/mh15189/mac |

## Data handling

| Fact | Source |
|---|---|
| Anthropic: "By default, we will not use your inputs or outputs from our commercial products" (page last updated 2026-08-18); feedback is the exception. | https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training |
| Anthropic, personal plans: chats, Claude Code included, are used for training when "Help improve our AI models" is on (checked 2026-11-12). Where the setting sits in claude.ai is still to confirm on the dry run. | https://code.claude.com/docs/en/data-usage |
| OpenAI: no training on ChatGPT Business, Enterprise or Edu content by default, Codex included; personal plans share data unless "Improve the model for everyone" is off. Seen in search results only: the pages block automated reading, so check by hand. | https://help.openai.com/en/articles/8798634 ; https://help.openai.com/en/articles/7730893-data-controls-in-chatgpt |
