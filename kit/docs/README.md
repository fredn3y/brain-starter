# Your brain: the one-page guide

This folder is your assistant's memory. It keeps who you are, your goals, the people you work
with, how you write and your tasks, so every chat starts from what it already knows about you. It
lives on your Mac, in your home folder, as `brain`.

## What it remembers

- `me.md`: who you are, your hours, and how you like to be told things.
- `strategy.md`: your goals, what you're focused on and what to ignore.
- `people.md`: the people you work with.
- `voice.md`: how you write, so drafts sound like you.
- `tasks/`: one file for each piece of work. It says what done looks like, the facts settled so
  far, who has the next move (you, your assistant or someone else) and a dated history. Anything
  you need to decide is written there with the background and a suggested answer, so it isn't lost
  in an old chat.

Tell your assistant when something changes ("the launch has moved to May", "I have a new
manager") and it updates the right file. You can also open the files and edit them yourself.

## Asking things

- Open T3 Code, choose your brain project and start a new thread with Claude.
- Ask in plain words: "What should I focus on today?", "Draft a reply to this" (paste the
  message in), "Where are we with the website?", "Add a task: renew the insurance by Friday".
- Ask "What needs me today?" for a short numbered list: deadlines, then the decisions waiting on
  you, each with a suggested answer, then anything you're waiting on. Reply by number, for example
  "1 yes, 2 not yet", and your assistant writes your answers down.
- To share a file, drop it in the `inbox` folder and say so.
- If you like, type `$` to pick a skill by name. You don't have to.

## What it can and can't do

It can keep your notes and tasks up to date, help you think things through, and write drafts for
you to send.

It can't:
- send email or post messages: you always press Send;
- delete or change your mail, files or calendar;
- see your passwords: you type them into a private Mac box, never into the chat.

Your chats go to Anthropic (for Claude) to be answered. On a personal Claude plan, Anthropic may
use them to improve its models if "Help improve our AI models" is on in your Claude privacy
settings at claude.ai. You can turn it off there.

## Your files and the kit's files

- **Yours:** `me.md`, `strategy.md`, `people.md`, `voice.md`, `tasks/`, `inbox/`, `briefs/` and your
  settings. Updates leave them alone, apart from the one-off change to task files described
  below. Edit them any time, or ask your assistant to.
- **The kit's:** `AGENTS.md`, `CLAUDE.md`, `docs/`, `tasks/TEMPLATE.md` and the built-in skills.
  Updates replace them. If you change one anyway, the update keeps your version and puts the new
  one next to it, ending in `.new`.
- Want your assistant to learn a new routine? Ask it to make you a skill. Your skills are named
  `my-…` and are yours to keep.

## Getting help

- In a brain chat, say "something's wrong". Your assistant runs a check and explains it plainly.
- In Terminal you can run the same check yourself: `brain doctor`.
- To get help from the person who set this up, run `brain report` (or ask your assistant to). It
  makes a file and shows it in Finder: email that file to them. It holds settings, check results
  and recent logs with emails and keys blanked out. Your mail, tasks and notes aren't in it.
- If Claude says your login has expired: open Terminal, type `claude`, log in, then type `/exit`.

## Updates

- When there's a new version, your assistant tells you at the start of a Claude chat. Say yes and
  it installs it.
- It saves a snapshot first and undoes the update by itself if anything goes wrong.
- Changed your mind later? Say "undo the update".
- On the update to version 0.3.0, your task files change to the new layout once. A copy of each
  old file is kept.

## Extras you can add

Ask in a brain chat when you want one:

- **"Connect Google"**: your assistant can read your Gmail, Calendar and Drive, and save email
  drafts in Gmail. It needs a company Google account and its Google Workspace admin; a personal
  Gmail can't be connected.
- **"Connect Slack"**: it can read and search your Slack. You need to be allowed to install an
  app there, or have an admin approve it.
- **"Turn on my morning brief"**: on workdays, a short brief of your meetings, the mail and
  messages waiting on you, and your tasks. It comes as a notification and a draft in your Gmail,
  or soon after you open your Mac if it was asleep. It needs Google first.
