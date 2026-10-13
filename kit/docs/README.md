# Your brain: the one-page guide

This folder is your work brain. It keeps your notes, tasks and daily briefs, and it gives your AI
assistant (Claude or Codex) the rules and know-how to help you. It lives on your Mac, in your home
folder, as `brain`.

## Your morning brief

- On workdays at your brief time, your brain writes a short brief: today's meetings, the mail and
  Slack messages waiting on you, tasks that are due, and a few suggestions.
- If your Mac was asleep or closed at that time, the brief arrives soon after you open it. After a
  long weekend you get one brief that covers the whole gap.
- You get a notification, a draft email to yourself in Gmail (look in Drafts), and the file in
  `briefs/`.
- Anything broken, and any update, is always at the top.

## Asking things

- Open T3 Code, choose your brain project and start a new thread with Claude or Codex.
- Ask in plain words: "What needs me today?", "Draft a reply to the supplier", "Where are we with
  the office move?", "Set up Slack again".
- If you like, type `$` to pick a skill by name. You don't have to.

## What it can and can't do

It can:
- read your Gmail, Google Calendar, Google Drive and Slack;
- keep your tasks and notes up to date;
- write drafts: emails go to your Gmail Drafts, Slack replies appear in the chat for you to paste.

It can't:
- send email or post to Slack: you always press Send;
- delete, archive or change your mail, files or calendar;
- see your passwords: you type them into a private Mac box, never into the chat.

## Your files and the kit's files

- **Yours:** `me.md`, `strategy.md`, `people.md`, `voice.md`, `tasks/`, `inbox/`, `briefs/` and your
  settings. Updates never touch them. Edit them any time, or ask your assistant to.
- **The kit's:** `AGENTS.md`, `CLAUDE.md`, `docs/`, `tasks/TEMPLATE.md` and the built-in skills.
  Updates replace them. If you change one anyway, the update keeps your version and puts the new
  one next to it, ending in `.new`.
- Want your assistant to learn a new routine? Ask it to make you a skill. Your skills are named
  `my-…` and are yours to keep.

## Getting help

- In a brain chat, say "something's wrong". Your assistant runs a check and explains it plainly.
- In Terminal you can run the same check yourself: `brain doctor`.
- To get help from your consultant, run `brain report` (or ask your assistant to). It makes a file
  and shows it in Finder: email that file to your consultant. It holds settings, check results and
  recent logs with emails and keys blanked out. Your mail, tasks, briefs and notes aren't in it.
- If Claude says your login has expired: open Terminal, type `claude`, log in, then type `/exit`.

## Updates

- When there's a new version, your brief says so at the top.
- In a brain chat, say "update". Your brain saves a snapshot first and undoes the update by itself
  if anything goes wrong.
- Changed your mind later? Say "undo the update".
