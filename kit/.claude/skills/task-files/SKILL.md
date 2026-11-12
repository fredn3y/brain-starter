---
name: task-files
description: Use when the user asks what needs them ("what needs me today?") or where work stands, when a chat brings new work, a decision, an answer or a wait, or to create, update, close, pause, reopen or archive a task file in tasks/.
---

# Task files

Each outcome is one file in `tasks/`. The file is the state: if it isn't written there, the next
session doesn't know it. Summaries are worked out from the files when asked, never stored. New
files start from `tasks/TEMPLATE.md`.

## The format

- Folders: `tasks/active/` (live), `tasks/later/` (the whole outcome paused), `tasks/archive/`
  (done). File names are short lowercase words joined by dashes: `march-offsite.md`.
- Front matter, every value quoted: `title`, `priority`, `created_at`, `updated_at` (today on
  every edit), `due_at` (a real deadline, or `""`), `completed_at` (set on archiving), `links`.
- `## Outcome`: what done looks like, in one or two sentences.
- `## Pinned`: settled facts, each ending with the date it was settled:
  `- The budget is £2,500. (2026-02-03)`. When a newer decision supersedes one, correct or remove
  it in the same edit.
- `## Threads / Open Loops`: one top-level bullet per open loop.
  - `- [ai] ...`: ready, concrete work for you, about three at most. Not ready until a date:
    `- [ai] From YYYY-MM-DD: ...`.
  - `- [human] ...`: the user's. A decision `Q: ...?`, an approval `Approve ...?` or a plain
    action. Each is a card (below).
  - `- [external] ...`: waiting on someone else, named, with an indented
    `Follow-Up: YYYY-MM-DD` line.
  - `- Deferred (on <trigger>; review by YYYY-MM-DD): ...`: future work nobody owns yet.
  - A finished thread is removed, with an Activity entry saying what closed it.
- `## Activity`: the dated journal, newest first. Each entry is a heading
  `### YYYY-MM-DD HH:MM UTC` (the time from `date -u`) with a bullet per thing that happened.
  Append only: add a new entry at the top; never edit, merge, summarise away or delete an old
  one. Long is fine.

## Cards

A card is a `[human]` thread with lines indented two spaces under it:

- `Context:` always: enough to decide cold, without opening anything else.
- `Options: Label :: consequence | Label :: consequence`: only for a real choice, at most four,
  the helpful default first.
- `Recommend:` on every question and approval: the suggested answer, so "yes" is enough.
- `Source:`, `Draft:`, `To:`, `Channel:` where they apply. `Snooze-Until: YYYY-MM-DD` when the
  user says "not yet".

One card, one decision. Rewrite an overlapping card rather than adding a second. If someone else
could settle it, it isn't a card: make it `[ai]` or `[external]`.

## Habits

- Anything the user must decide or approve becomes a card in the task file, with context and a
  suggested answer. Never left only in chat.
- Recording the user's answer closes or rewrites every card it settles, in any task file, in the
  same edit, with a dated Activity entry in each.
- If the next session would be worse without it, write it back now: threads, any Pinned fact it
  supersedes, an Activity entry and `updated_at` together. Then re-read the file to confirm.
- Nothing consequential disappears. Close a card or a wait only on evidence (their answer, a
  message, a file) and name it in the Activity entry. Say out loud anything you tidied away.
- Every wait has a follow-up date. If nobody gave one, suggest one.
- A card left unanswered for three working days (`workdays` in `.kit/config.json`), counted from
  the Activity entry that raised it or its last snooze, is rechecked, never silently dropped: is it
  still needed, still true, answered somewhere else? Refresh it or close it on evidence, and say
  which.
- In chat, speak plainly: "needs you", "waiting on the printer", "parked until Monday". Never
  show tags, field names or file paths unless asked. The user never has to type a tag. Dates in
  files are `YYYY-MM-DD`; in chat, "2 Oct".

## Create, update, close, pause, reopen

- **Create.** Grep `tasks/` first: if a file already covers the outcome, update that one. Copy the
  template to `tasks/active/<short-name>.md`, fill it in and delete its notes. The first Activity
  entry says `Created.` and where the work came from.
- **Priority.** `P2` unless the user says otherwise. `P1` only with a real `due_at` or a named
  blocker, and the user's OK; about three at most. `P3` is nice to have.
- **Close.** Activity entry `Done.` with one line on how, set `completed_at`, then move it with
  `mv` to `tasks/archive/`. If the name is taken, add `-YYYY-MM-DD` before `.md`.
- **Pause.** Only when the whole outcome is on hold; one stuck thread is not a pause. Activity entry
  saying why and when to look again, then move it to `tasks/later/`. It stays there until the user
  or a real event brings it back.
- **Reopen.** Move it back to `tasks/active/` with an Activity entry.
- Never delete a task file. After any change, tell the user in one plain line what changed.

## Find

- Live work: Glob `tasks/active/*.md`. Look in `tasks/later/` or `tasks/archive/` only when the
  user asks, or nothing live matches. Grep a person, company or topic across `tasks/`.
- "Where are we with X?": three plain lines: the outcome, where it stands (the latest Activity
  line), what's next and who has it.

## "What needs me?"

When the user asks what needs them (today, this week), answer from the files in `tasks/active/`
alone, short and numbered so they can reply by number:

1. Deadlines first: anything due or overdue, soonest first.
2. Then each open card, one line in plain words with its suggested answer: "2. Which venue for the
   offsite? I'd say the hotel: it fits the budget." Leave out cards snoozed to a later date.
3. Then each wait whose follow-up date has come, and each deferred thread whose review date has,
   asked as a question, never asserted: "4. Still waiting on the printer for the proof?"
4. One closing line saying what this is based on and what you can't see, for example "From your
   task files only. I can't see your mail or calendar."

Never say "nothing waiting", "free" or "clear" about anything you can't see. If no file has
anything for them, say exactly that: "Nothing in your task files needs you today."

Take replies like "1 yes, 2 not yet, 4 it came" and write each answer back under the habits
above: "yes" takes the suggested answer; "not yet" keeps the card with a `Snooze-Until` date
(suggest one); a wait gets a new follow-up date or closes. An approval with a draft: show the full
draft before taking its yes. Then confirm in one plain line per number.
