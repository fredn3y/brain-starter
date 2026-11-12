# Onboard add-ons

Run one only when the user asks. First check what it needs (listed in `SKILL.md`); if something is
missing, say so plainly and stop. Before it, say in one line what it's for; after it, what changed.

## google (15 min)

1. Ask: "Is your email a company Google account, and are you, or someone helping you, its Google
   Workspace admin?" If not, explain that a personal Gmail can't be connected, and stop.
2. Say: "Your brain needs its own private Google sign-in to read your mail, calendar and Drive.
   You'll make it in Google Cloud with Claude in Chrome; it stays inside your company."
3. Show prompt B from `docs/setup-prompts.md` in one code block. The user pastes it into Claude in
   Chrome, does the "your turn" steps themselves and comes back with "done with Google".
4. Ask them to open Terminal and run `brain google connect` there (it fetches the Google tool if
   needed, and waits up to 10 minutes for the sign-in, longer than a chat command can). It finds
   the downloaded file and opens Google's sign-in page; they sign in with their company account,
   approve access and say "done". File not found: ask where it is, then have them run
   `brain google connect --client-file PATH`.
5. Run `brain google status`: Gmail, Calendar and Drive should be connected. Then offer `refresh`.

If it fails: `org_internal` = a personal or other non-company account, so connect again with the
company account. "Access blocked" with "response_type": connect again; don't click links in
Terminal. An admin or policy error: show the "If Google blocks the sign-in" prompt, wait (up to
24 hours), connect again.

## slack (10 min)

1. Run `brain slack create-app`: Slack's create-app page opens with the settings filled in.
   Claude in Chrome only works in Chrome: if it opened elsewhere, the user copies the address over.
2. Show prompt C from `docs/setup-prompts.md`. They come back with "done with Slack".
3. Run `brain secret set slack --gui`. A private Mac box opens; they paste the token (it starts
   with `xoxp-`) and click OK. You never see it.
4. Run `brain slack whoami`: confirm the workspace and their name. Token rejected: repeat step 3.
   A missing permission: repeat steps 1-3. Then offer `refresh`.

## brief (5 min)

1. Run `brain google status`. Not connected: offer `google` first.
2. Ask what time they'd like the brief; set `brief_time` (like `"08:00"`) in `.kit/config.json`,
   keeping the JSON valid.
3. Run `brain schedule install`. If macOS says a background item was added, it must stay allowed
   (System Settings, General, Login Items; Login Items & Extensions on newer macOS).
4. Run `brain notify-test` and ask if they saw it. If not: System Settings, Notifications, Script
   Editor, turn on Allow notifications, and test again.
5. Run `brain brief --detach` (it returns at once; the brief takes 1-3 minutes). After a minute or
   two, show the newest file in `briefs/`. Save what they'd change under "How I like to be told
   things" in `me.md`.
6. Tell them: "Your brief comes on workdays at <brief time>, or soon after you open your Mac. It's
   also a draft in your Gmail."

## refresh (after Google or Slack is connected)

Offer each in turn; the user can skip any. Use only the sources that are connected. Propose only
what isn't already in the files, and save after the user's OK.

### people

1. Candidates from the last 30 days:
   - mail: `gws gmail +triage --format json --max 100 --query "newer_than:30d -category:promotions -category:social"`
   - Slack DMs: `brain slack recent --hours 720 --dms --json`
   - meetings: `gws calendar +agenda --days 14 --format json --timezone <the user's timezone>`
2. Propose the 10-20 who come up most: name, email or Slack name, likely role, why they matter.
   Five at a time; the user confirms, corrects or drops each.
3. Add the confirmed people to `people.md` in its format: only what the user said or what the mail
   makes plain (role, company), nothing personal.

### voice

1. Sent mail: `gws gmail +triage --format json --max 15 --query "in:sent newer_than:60d"`, then
   `gws gmail +read --id MESSAGE_ID --format json` for each. Skip health, HR, legal and personal
   money; ignore quoted text from others.
2. Slack: `brain slack whoami` gives their user ID; pick their messages from
   `brain slack recent --hours 336 --json`.
3. Update `voice.md` where the new samples add something. Describe the style; don't copy whole
   messages.

### tasks

1. If Slack is connected, run `brain slack sync --lookback-days 14`.
2. Read 14 days of mail (`--query "newer_than:14d -category:promotions -category:social"`), the
   user's sent mail for promises (`--query "in:sent newer_than:14d"`) and Slack
   (`brain slack recent --hours 336 --mentions --json`, then the same with `--dms`).
3. Propose 5-10 tasks, one line each: title, outcome, next step and owner, source. The user keeps
   or drops each. Create the kept ones with the `task-files` skill, the source in the first
   Activity entry and on any card.
