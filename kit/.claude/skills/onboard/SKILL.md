---
name: onboard
description: Use for first-time setup of the brain, or to redo one part of it - health check, Google, Slack, the getting-to-know-you interview, people, writing voice, first tasks, the daily schedule, backups. Runs every part in order, or only the part the user names (for example "onboard google" or "onboard strategy").
---

# Onboard

Guide the user through setup one step at a time, in plain words. Parts: `check`, `claude`,
`google`, `slack`, `interview` (also `strategy`), `people`, `voice`, `tasks`, `schedule`, `backup`,
`wrap`. If they name a part, run only that part. Otherwise go in order and skip parts already done
(doctor says ok, or the file has no `{{` left). Before each part, say in one line what it's for
and how long it takes; after it, say in one line what changed.

## 0. check (2 min)

1. Run `brain doctor --json`. Explain each `warn` or `fail` in one plain line with its fix.
2. If `claude trust` or `claude login` isn't ok: the user opens Terminal (Cmd+Space, type Terminal,
   Return) and runs `cd ~/brain && claude`. When Claude asks whether they trust the folder: yes.
   If it asks how to log in: Claude account with subscription, their company's Team plan,
   Authorize. Then they type `/exit`. Run `brain doctor --json` again.

## 1. claude (only if needed)

Skip if `claude login` is ok. If Claude says "You haven't been added to your organization yet",
the user's seat lacks Claude Code: show prompt A from `docs/setup-prompts.md` in one code block,
for them to send to the person who manages the company's Claude plan.

## 2. google (15 min)

1. Say: "Your brain needs its own private Google sign-in to read your mail, calendar and Drive.
   You'll make it in Google Cloud with Claude in Chrome; it stays inside your company."
2. Show prompt B from `docs/setup-prompts.md` in one code block. The user pastes it into Claude in
   Chrome, does the "your turn" steps themselves and comes back with "done with Google".
3. Ask them to open Terminal and run `brain google connect` there (it waits up to 10 minutes for
   the sign-in, longer than a chat command can). It finds the downloaded file and opens Google's
   sign-in page; they sign in with their work account, approve access and say "done". File not
   found: ask where it is, then have them run `brain google connect --client-file PATH`.
4. Run `brain google status`: Gmail, Calendar and Drive should be connected.

If it fails: `org_internal` = a non-work account, so connect again with their work account. "Access
blocked" with "response_type": connect again; don't click links in Terminal. An admin or policy
error: show the "If Google blocks the sign-in" prompt, wait (up to 24 hours), connect again.

## 3. slack (10 min)

1. Run `brain slack create-app`: Slack's create-app page opens with the settings filled in.
   Claude in Chrome only works in Chrome: if it opened elsewhere, the user copies the address over.
2. Show prompt C from `docs/setup-prompts.md`. They come back with "done with Slack".
3. Run `brain secret set slack --gui`. A private Mac box opens; they paste the token (it starts
   with `xoxp-`) and clicks OK. You never see it.
4. Run `brain slack whoami`: confirm the workspace and their name. Token rejected: repeat step 3.
   A missing permission: repeat steps 1-3.

## 4. interview (15 min): me.md and strategy.md

One question at a time; wait for each answer. "Skip" is fine.

1. What's your role, and what are you responsible for?
2. What does the company do, and for whom? Roughly how big is it?
3. Who's on your team, and who do you report to?
4. What does a good week look like? And a good quarter?
5. What's on fire right now?
6. What should I ignore?
7. Your working days, hours and timezone? When would you like the brief?
8. How do you like to be told things: short or detailed, how direct, when to interrupt you?

Fill `me.md` and `strategy.md`: replace every `{{placeholder}}` (none left), keep the headings,
delete the `<!-- -->` notes. Show each file in full; save only after the user's OK. If their
answers differ from `.kit/config.json`, update `timezone` (like `Europe/Paris`), `workdays`
(Monday = 1), `work_hours` (like `[9, 17]`) and `brief_time` (like `"08:00"`), keeping the JSON
valid. If the schedule is already installed, run `brain schedule install` again.

## 5. people (10 min)

1. Candidates from the last 30 days:
   - mail: `gws gmail +triage --format json --max 100 --query "newer_than:30d -category:promotions -category:social"`
   - Slack DMs: `brain slack recent --hours 720 --dms --json`
   - meetings: `gws calendar +agenda --days 14 --format json --timezone <the user's timezone>`
2. Propose the 10-20 who come up most: name, email or Slack name, likely role, why they matter.
   Five at a time; the user confirms, corrects or drops each.
3. Write the confirmed people into `people.md` in its format: only what the user said or what the
   mail makes plain (role, company), nothing personal.

## 6. voice (5 min)

1. The user's sent mail: `gws gmail +triage --format json --max 15 --query "in:sent newer_than:60d"`,
   then `gws gmail +read --id MESSAGE_ID --format json` for each. Skip health, HR, legal and
   personal money; ignore quoted text from others.
2. Their Slack: `brain slack whoami` gives their user ID; pick their messages from
   `brain slack recent --hours 336 --json`.
3. Fill `voice.md`: greeting, sign-off, length, tone, phrases they use and avoid, email versus
   Slack, changes by audience. Describe the style; don't copy whole messages. Save after their OK.

## 7. tasks (10 min)

1. Run `brain slack sync --lookback-days 14`.
2. Read 14 days of mail (`--query "newer_than:14d -category:promotions -category:social"`),
   the user's sent mail for promises (`--query "in:sent newer_than:14d"`) and Slack
   (`brain slack recent --hours 336 --mentions --json`, then the same with `--dms`).
3. Propose 5-10 tasks, one line each: title, outcome, next step and owner, source. The user keeps or
   drops each. Create the kept ones from `tasks/TEMPLATE.md` (`task-files` skill), the source in the Log.

## 8. schedule (5 min)

1. Run `brain schedule install`. If macOS says a background item was added, it must stay allowed
   (System Settings, General, Login Items; Login Items & Extensions on newer macOS).
2. Run `brain notify-test` and ask if they saw it. If not: System Settings, Notifications, Script
   Editor, turn on Allow notifications, and test again.
3. Run `brain brief --detach` (it returns at once; the brief takes 1-3 minutes). After a minute or
   two, show the newest file in `briefs/`. Save what they'd change under "How I like to be told
   things" in `me.md`.

## 9. backup (1 min)

If doctor's `time machine` check warns, suggest Time Machine (System Settings, General, Time
Machine): this folder holds their notes and tasks.

## 10. wrap (1 min)

Five lines, no more:
- Your brief comes on workdays at <brief time>, or soon after you open your Mac; it's also a draft
  in your Gmail.
- To ask anything: T3 Code, your brain project, a new Claude or Codex thread. Plain words.
- I read and draft; you always send.
- Something wrong? Say so, or run `brain doctor`. `brain report` makes a file for your consultant.
- Updates: your brief tells you; just say "update".
