---
name: onboard
description: Use for first-time setup ("set me up") or to redo one part - the health check, Claude login, the getting-to-know-you interview, people, writing voice, first tasks. Also use when the user asks to connect Google, connect Slack or turn on the morning brief, which are add-ons run only on request.
---

# Onboard

Set up the brain one step at a time, in plain words. One question at a time; wait for each
answer. "Skip" is always fine.

The default setup is memory only and takes about 30 minutes: `check`, `claude`, `interview`,
`people`, `voice`, `tasks`, `wrap`. Go in order and skip parts already done (doctor says ok, or
the file has no `{{` left). If the user names a part ("onboard voice"), run only that part. Before
each part, say in one line what it's for; after it, say in one line what changed.

Google, Slack and the morning brief are add-ons: `google`, `slack`, `brief`. Run one only when
the user asks ("connect Google", "connect Slack", "turn on my morning brief"), never as part of the
default setup. Check what it needs first, and say plainly if something is missing:
- `google`: a company Google account and a Google Workspace admin (the user, or one helping them).
  A personal Gmail can't be connected.
- `slack`: the right to install an app in their Slack workspace, or an admin who will approve it.
- `brief`: Google connected first.

The add-on steps are in `add-ons.md` in this skill's folder. Once Google or Slack is connected,
its `refresh` part fills in people, voice and tasks from mail and Slack.

## check (2 min)

Run `brain doctor --json`. Explain each `warn` or `fail` in one plain line with its fix. Google,
Slack and the schedule showing as not set up is normal: don't mention them.

## claude (only if needed, 3 min)

If `claude trust` or `claude login` isn't ok: the user opens Terminal (Cmd+Space, type Terminal,
Return) and runs `cd ~/brain && claude`. When Claude asks whether they trust the folder: yes. To
log in: Claude account with subscription, then sign in with their Claude account and Authorize.
Any paid plan works (Pro, Max or Team). Then they type `/exit`. Run `brain doctor --json` again.

On a Team plan, "You haven't been added to your organization yet" means the seat lacks Claude
Code: show prompt A from `docs/setup-prompts.md` in one code block, for whoever manages the plan.

## interview (10 min): me.md and strategy.md

1. What do you do, and who for? (Your role, and the business, employer or clients you work for.)
2. What are you responsible for? What do people count on you for?
3. Who do you work for or report to, and who works with you? Names are enough for now.
4. What would make the next three months a success? And what does a good week look like?
5. What's on fire right now?
6. What should I ignore, or help you say no to?
7. Your working days, hours and timezone?
8. How do you like to be told things: short or detailed, how direct?

Fill `me.md` and `strategy.md`: replace every `{{placeholder}}` (write "not set yet" for a
skipped one), keep the headings, delete the `<!-- -->` notes. Show each file in full; save only
after the user's OK. If their answers differ from `.kit/config.json`, update `timezone` (like
`Europe/Paris`), `workdays` (Monday = 1) and `work_hours` (like `[9, 17]`), keeping the JSON
valid. If the morning brief is on, run `brain schedule install` again so it uses the new times.

## people (5 min): people.md

1. Ask: "Who do you work with most? Name a few and say who each one is to you." Take what they
   give; ask at most one follow-up per person.
2. Replace the template block in `people.md` with one block per person: only what the user said,
   nothing personal. Leave out a line you don't know rather than guessing. Show it; save after
   their OK.

## voice (5 min): voice.md

1. Ask for two or three things they wrote recently and are happy with (an email, a message), pasted
   into the chat. If they'd rather not, skip this part; "onboard voice" does it later.
2. If the samples don't show it, ask how they greet and sign off.
3. Fill `voice.md`: greeting, sign-off, length, tone, phrases they use and avoid, email versus chat
   messages, changes by audience. Describe the style; don't save the samples. Write "not known
   yet" where you can't tell. Show it; save after their OK.

## tasks (5 min)

1. Ask: "What's on your plate? Anything you're working on, waiting for, or have promised someone."
2. Turn it into tasks, one plain line each: the outcome, the next step and who has it. One outcome
   per task. The user keeps, changes or drops each.
3. Create the kept ones with the `task-files` skill, "From setup" in the first Activity entry.
   Anything they must decide or do becomes a card with context and a suggested answer; anything
   they're waiting for gets a follow-up date.
4. Answer "what needs me today?" from the new files, so they see what that question gives them.

## wrap (2 min)

Five lines, no more:
- I now know who you are, what matters and who you work with. Tell me when something changes.
- To ask anything: T3 Code, your brain project, a new Claude thread. Plain words. Try "what needs
  me today?".
- I draft; you always send.
- Something wrong? Say so, or run `brain doctor`. `brain report` makes a file for whoever helps you.
- I'll tell you at the start of a chat when there's an update. Later I can also connect Google or
  Slack, or send you a morning brief: just ask.

If doctor's `time machine` check warns, add one line suggesting Time Machine (System Settings,
General, Time Machine): this folder holds their notes and tasks.
